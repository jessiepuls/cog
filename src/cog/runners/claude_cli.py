import asyncio
import json
import os
import sys
import tempfile
import time
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from subprocess import PIPE
from typing import Any

from cog.core.errors import RunnerStalledError, RunnerTimeoutError, StreamJsonParseError
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


def _parse_float_env(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        print(
            f"WARNING: {name}={raw!r} is not a valid number; defaulting to {default}",
            file=sys.stderr,
        )
        return default


def _parse_int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        print(
            f"WARNING: {name}={raw!r} is not a valid integer; defaulting to {default}",
            file=sys.stderr,
        )
        return default


# 16 MiB: 256× the observed worst-case line (~64 KiB). Per-line RAM is
# transient — the buffer is cleared after each line is parsed.
_STREAM_LINE_LIMIT_BYTES = _parse_int_env("COG_STREAM_LINE_LIMIT_BYTES", 16 * 1024 * 1024)


class ClaudeCliRunner(AgentRunner):
    def __init__(self, sandbox: Sandbox) -> None:
        self._sandbox = sandbox
        self._timeout_seconds = _parse_float_env("COG_RUNNER_TIMEOUT_SECONDS", 1800.0)
        # Idle timeout: no outstanding tool calls AND no new stream events.
        self._inactivity_timeout_seconds = _parse_float_env(
            "COG_RUNNER_INACTIVITY_TIMEOUT_SECONDS", 300.0
        )
        # Tool-call timeout: a tool is in progress (tool_use without matching tool_result).
        self._tool_call_timeout_seconds = _parse_float_env(
            "COG_RUNNER_TOOL_CALL_TIMEOUT_SECONDS", 600.0
        )
        self._sigterm_grace_seconds = 5.0

    async def stream(
        self, prompt: str, *, model: str, cwd: Path | None = None
    ) -> AsyncIterator[RunEvent]:
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
            ],
            cwd=cwd,
        )
        env = self._sandbox.wrap_env(dict(os.environ))

        proc = await asyncio.create_subprocess_exec(
            *argv, env=env, stdout=PIPE, stderr=PIPE, limit=_STREAM_LINE_LIMIT_BYTES
        )
        assert proc.stdout is not None  # guaranteed by stdout=PIPE
        # Drain stderr concurrently so the pipe buffer (~64KB on Linux) can't fill and
        # block claude's stderr writes — which would cascade into stdout stalling and
        # trip RunnerStalledError (#48). Real stderr content is discarded in this
        # minimal drain; #44 layers on capture-for-diagnostics.
        stderr_drain: asyncio.Task[bytes] | None = (
            asyncio.create_task(proc.stderr.read()) if proc.stderr is not None else None
        )
        start = time.monotonic()

        final_text = ""
        result_record: dict[str, Any] | None = None
        last_event_summary: str | None = None
        _waited = False
        _proc_cleaned = False
        outstanding_tool_ids: set[str] = set()
        outstanding_tool_names: dict[str, str] = {}  # id → name, for stall diagnostics

        try:
            async with asyncio.timeout(self._timeout_seconds):
                while True:
                    timeout = (
                        self._tool_call_timeout_seconds
                        if outstanding_tool_ids
                        else self._inactivity_timeout_seconds
                    )
                    try:
                        raw_line = await asyncio.wait_for(
                            proc.stdout.readline(),
                            timeout=timeout,
                        )
                    except TimeoutError:
                        proc.terminate()
                        try:
                            await asyncio.wait_for(proc.wait(), timeout=self._sigterm_grace_seconds)
                        except TimeoutError:
                            proc.kill()
                            await proc.wait()
                        _proc_cleaned = True
                        _waited = True
                        raise RunnerStalledError(
                            inactivity_seconds=timeout,
                            last_event_summary=last_event_summary,
                        ) from None

                    if not raw_line:
                        break  # EOF

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

                    record_type = record.get("type")

                    if record_type == "assistant":
                        for block in record.get("message", {}).get("content", []):
                            if block.get("type") == "tool_use":
                                tool_id = block.get("id", "")
                                if tool_id:
                                    outstanding_tool_ids.add(tool_id)
                                    outstanding_tool_names[tool_id] = block.get("name", "")
                    elif record_type == "user":
                        for block in record.get("message", {}).get("content", []):
                            if block.get("type") == "tool_result":
                                tool_id = block.get("tool_use_id", "")
                                drained_name = outstanding_tool_names.pop(tool_id, None)
                                outstanding_tool_ids.discard(tool_id)
                                if drained_name and not outstanding_tool_ids:
                                    last_event_summary = f"completed: {drained_name}"

                    for event in _record_to_events(record):
                        if isinstance(event, AssistantTextEvent):
                            final_text = event.text
                            last_event_summary = f"assistant: {event.text[:80]}"
                        elif isinstance(event, ToolUseEvent):
                            cmd_or_path = (
                                event.input.get("command") or event.input.get("file_path") or ""
                            )
                            last_event_summary = f"in-progress: {event.tool}: {cmd_or_path[:120]}"
                        yield event
        except TimeoutError:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=self._sigterm_grace_seconds)
            except TimeoutError:
                proc.kill()
                await proc.wait()
            _waited = True
            raise RunnerTimeoutError(f"claude exceeded {self._timeout_seconds}s") from None
        except Exception:
            if not _proc_cleaned:
                proc.kill()
                await proc.wait()
            _waited = True
            raise
        finally:
            if not _waited:
                await proc.wait()
            if stderr_drain is not None and not stderr_drain.done():
                stderr_drain.cancel()
            if stderr_drain is not None:
                try:
                    await stderr_drain
                except (asyncio.CancelledError, Exception):
                    pass

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
