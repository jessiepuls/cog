import asyncio
import json
import os
import tempfile
import time
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from subprocess import PIPE
from typing import Any

from cog.core.errors import RunnerTimeoutError, StreamJsonParseError
from cog.core.runner import (
    AgentRunner,
    AssistantTextEvent,
    ResultEvent,
    RunEvent,
    RunResult,
    ToolUseEvent,
)
from cog.core.sandbox import Sandbox


def _parse_content_block(block: dict[str, Any]) -> RunEvent | None:
    t = block.get("type")
    if t == "text":
        return AssistantTextEvent(text=block.get("text", ""))
    if t == "tool_use":
        return ToolUseEvent(tool=block.get("name", ""), input=block.get("input", {}))
    return None


def _record_to_events(record: dict[str, Any]) -> Iterator[RunEvent]:
    if record.get("type") != "assistant":
        return
    for block in record.get("message", {}).get("content", []):
        event = _parse_content_block(block)
        if event is not None:
            yield event


def _append_line(path: Path, line: str) -> None:
    with open(path, "a") as f:
        f.write(line + "\n")


class ClaudeCliRunner(AgentRunner):
    def __init__(self, sandbox: Sandbox) -> None:
        self._sandbox = sandbox
        self._timeout_seconds = float(os.environ.get("COG_RUNNER_TIMEOUT_SECONDS", "1800"))

    async def stream(self, prompt: str, *, model: str) -> AsyncIterator[RunEvent]:
        await self._sandbox.prepare()

        _fd, tmp = tempfile.mkstemp(suffix=".jsonl", prefix="cog-claude-")
        os.close(_fd)
        stream_path = Path(tmp)

        argv = self._sandbox.wrap_argv(
            [
                "claude",
                "--print",
                "--output-format",
                "stream-json",
                "--verbose",
                "--dangerously-skip-permissions",
                "--model",
                model,
                prompt,
            ]
        )
        env = self._sandbox.wrap_env(dict(os.environ))

        proc = await asyncio.create_subprocess_exec(*argv, env=env, stdout=PIPE, stderr=PIPE)
        assert proc.stdout is not None  # guaranteed by stdout=PIPE
        start = time.monotonic()

        final_text = ""
        result_record: dict[str, Any] | None = None
        _waited = False

        try:
            async with asyncio.timeout(self._timeout_seconds):
                async for raw_line in proc.stdout:
                    line = raw_line.decode("utf-8", errors="replace").rstrip("\n")
                    if not line:
                        continue
                    _append_line(stream_path, line)
                    try:
                        record: dict[str, Any] = json.loads(line)
                    except json.JSONDecodeError as exc:
                        raise StreamJsonParseError(f"bad JSON line: {line!r}") from exc
                    if record.get("type") == "result":
                        result_record = record
                        continue
                    for event in _record_to_events(record):
                        if isinstance(event, AssistantTextEvent):
                            final_text = event.text
                        yield event
        except TimeoutError:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=5.0)
            except TimeoutError:
                proc.kill()
                await proc.wait()
            _waited = True
            raise RunnerTimeoutError(f"claude exceeded {self._timeout_seconds}s") from None
        except Exception:
            proc.kill()
            await proc.wait()
            _waited = True
            raise
        finally:
            if not _waited:
                await proc.wait()

        duration = time.monotonic() - start
        cost = float(result_record.get("total_cost_usd", 0.0)) if result_record else 0.0
        exit_status = (
            int(result_record.get("exit_status", 0)) if result_record else (proc.returncode or 0)
        )

        yield ResultEvent(
            result=RunResult(
                final_message=final_text,
                total_cost_usd=cost,
                exit_status=exit_status,
                stream_json_path=stream_path,
                duration_seconds=duration,
            )
        )
