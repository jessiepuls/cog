"""Shared test helpers for ClaudeCliRunner tests."""

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "claude"


class FakeProcess:
    """Fake asyncio subprocess process that streams fixture data."""

    def __init__(self, stdout: Any, returncode: int = 0) -> None:
        self.stdout = stdout
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


async def _hanging_stdout() -> AsyncIterator[bytes]:
    yield b'{"type":"system","subtype":"init"}\n'
    await asyncio.sleep(10)


def hanging_proc() -> FakeProcess:
    """Fake process whose stdout never closes — for timeout tests."""
    proc = FakeProcess(_hanging_stdout(), returncode=0)

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


class RecordingSandbox:
    """Sandbox that records calls for assertion in tests."""

    def __init__(self) -> None:
        self.prepare_calls = 0
        self.wrap_argv_args: list[list[str]] = []
        self.wrap_env_args: list[dict[str, str]] = []

    async def prepare(self) -> None:
        self.prepare_calls += 1

    def wrap_argv(self, argv: Any) -> list[str]:
        result = list(argv)
        self.wrap_argv_args.append(result)
        return result

    def wrap_env(self, env: Any) -> dict[str, str]:
        result = dict(env)
        self.wrap_env_args.append(result)
        return result
