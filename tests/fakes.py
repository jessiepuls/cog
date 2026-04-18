from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

from cog.core.item import Item
from cog.core.runner import AgentRunner, ResultEvent, RunEvent, RunResult


class EchoRunner(AgentRunner):
    """Returns the prompt as the final_message. Zero cost, zero duration."""

    async def stream(self, prompt: str, *, model: str) -> AsyncIterator[RunEvent]:
        yield ResultEvent(
            result=RunResult(
                final_message=prompt,
                total_cost_usd=0.0,
                exit_status=0,
                stream_json_path=Path("/dev/null"),
                duration_seconds=0.0,
            )
        )


class InMemoryStateCache:
    """Dict-backed StateCache. Structurally satisfies the StateCache Protocol."""

    def __init__(self) -> None:
        self._processed: dict[str, str] = {}
        self._deferred: dict[str, dict[str, object]] = {}

    def _key(self, item: Item) -> str:
        return f"{item.tracker_id}:{item.item_id}"

    def is_processed(self, item: Item) -> bool:
        return self._key(item) in self._processed

    def mark_processed(self, item: Item, outcome: str) -> None:
        self._processed[self._key(item)] = outcome

    def is_deferred(self, item: Item) -> bool:
        return self._key(item) in self._deferred

    def mark_deferred(self, item: Item, reason: str, blockers: list[str]) -> None:
        self._deferred[self._key(item)] = {"reason": reason, "blockers": blockers}

    def clear_deferral(self, item: Item) -> None:
        self._deferred.pop(self._key(item), None)

    def save(self) -> None:
        pass


@dataclass
class SubprocessCall:
    argv: tuple[str, ...]
    cwd: Path | None
    stdin: bytes | None = None  # populated after communicate()


class _FakeProcess:
    def __init__(self, stdout: bytes, returncode: int, call_record: SubprocessCall) -> None:
        self._stdout = stdout
        self.returncode = returncode
        self._call_record = call_record

    async def communicate(self, input: bytes | None = None) -> tuple[bytes, bytes]:
        self._call_record.stdin = input
        return self._stdout, b""


class FakeSubprocessRegistry:
    """Queue-based fake for asyncio.create_subprocess_exec.

    Push responses in order, then use patch() as a context manager to intercept
    subprocess calls. Inspect calls[] after the fact to assert argv/cwd/stdin.
    """

    def __init__(self) -> None:
        self._queue: list[tuple[bytes, int]] = []
        self.calls: list[SubprocessCall] = []

    def push(self, *, stdout: bytes = b"", returncode: int = 0) -> None:
        self._queue.append((stdout, returncode))

    async def _create(self, *args: Any, **kwargs: Any) -> _FakeProcess:
        stdout, returncode = self._queue.pop(0) if self._queue else (b"", 0)
        call = SubprocessCall(argv=tuple(str(a) for a in args), cwd=kwargs.get("cwd"))
        self.calls.append(call)
        return _FakeProcess(stdout, returncode, call)

    def patch(self, module_path: str) -> Any:
        return patch(
            f"{module_path}.asyncio.create_subprocess_exec",
            new=AsyncMock(side_effect=self._create),
        )
