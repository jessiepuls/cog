"""Shared test helpers for ClaudeCliRunner tests."""

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "claude"


class FakeProcess:
    """Fake asyncio subprocess process that streams fixture data."""

    def __init__(self, stdout: Any, returncode: int = 0) -> None:
        self.stdout = stdout
        self.stderr = None  # ClaudeCliRunner guards against None stderr for mocks.
        self.returncode = returncode
        self.terminated = False
        self.killed = False

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True

    async def wait(self) -> int:
        return self.returncode


def _make_reader(data: bytes) -> asyncio.StreamReader:
    reader = asyncio.StreamReader()
    reader.feed_data(data)
    reader.feed_eof()
    return reader


def fixture_proc(name: str, returncode: int = 0) -> FakeProcess:
    data = (FIXTURES_DIR / name).read_bytes()
    return FakeProcess(_make_reader(data), returncode)


class _HangingStdout:
    """Stdout that hangs forever on readline() — for wall-clock timeout tests."""

    async def readline(self) -> bytes:
        await asyncio.sleep(10)
        return b""


def hanging_proc() -> FakeProcess:
    """Fake process whose stdout never closes — for timeout tests."""
    proc = FakeProcess(_HangingStdout(), returncode=0)

    def _terminate() -> None:
        proc.terminated = True
        proc.returncode = -15

    def _kill() -> None:
        proc.killed = True
        proc.returncode = -9

    proc.terminate = _terminate  # type: ignore[method-assign]
    proc.kill = _kill  # type: ignore[method-assign]
    return proc


def patch_exec(proc: FakeProcess) -> Any:
    """Context manager that patches asyncio.create_subprocess_exec to return proc."""
    return patch(
        "cog.runners.claude_cli.asyncio.create_subprocess_exec",
        new=AsyncMock(return_value=proc),
    )


@dataclass
class StreamEvent:
    """A scripted event for PausingMockProc."""

    data: bytes
    delay_before: float = 0.0


class _PausingStdout:
    def __init__(self, proc: "PausingMockProc", events: list[StreamEvent]) -> None:
        self._proc = proc
        self._events = events
        self._idx = 0

    async def readline(self) -> bytes:
        if self._idx >= len(self._events):
            self._signal_eof()
            return b""
        event = self._events[self._idx]
        self._idx += 1
        if event.delay_before > 0:
            await asyncio.sleep(event.delay_before)
        if not event.data:
            self._signal_eof()
        return event.data

    def _signal_eof(self) -> None:
        if self._proc.returncode is None:
            self._proc.returncode = 0
            self._proc._done.set()


class PausingMockProc:
    """Mock asyncio subprocess that emits scripted events with configurable delays.

    Use StreamEvent(b'', delay_before=9999) to simulate an infinite hang.
    """

    def __init__(
        self,
        events: list[StreamEvent],
        *,
        respects_sigterm: bool = True,
    ) -> None:
        self._respects_sigterm = respects_sigterm
        self._terminated = False
        self._killed = False
        self.returncode: int | None = None
        self._done = asyncio.Event()
        self.stdout = _PausingStdout(self, events)
        self.stderr = None

    def terminate(self) -> None:
        self._terminated = True
        if self._respects_sigterm:
            self.returncode = 143
            self._done.set()

    def kill(self) -> None:
        self._killed = True
        self.returncode = 137
        self._done.set()

    async def wait(self) -> int:
        if self.returncode is not None:
            return self.returncode
        await self._done.wait()
        return self.returncode  # type: ignore[return-value]


def patch_pausing_exec(proc: "PausingMockProc") -> Any:
    """Context manager that patches asyncio.create_subprocess_exec to return a PausingMockProc."""
    return patch(
        "cog.runners.claude_cli.asyncio.create_subprocess_exec",
        new=AsyncMock(return_value=proc),
    )


class RecordingSandbox:
    """Sandbox that records calls for assertion in tests."""

    def __init__(self) -> None:
        self.prepare_calls = 0
        self.wrap_argv_args: list[list[str]] = []
        self.wrap_env_args: list[dict[str, str]] = []

    async def prepare(self) -> None:
        self.prepare_calls += 1

    def wrap_argv(self, argv: Any, cwd: Any = None) -> list[str]:
        result = list(argv)
        self.wrap_argv_args.append(result)
        return result

    def wrap_env(self, env: Any) -> dict[str, str]:
        result = dict(env)
        self.wrap_env_args.append(result)
        return result
