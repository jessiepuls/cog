"""Tests for ClaudeCliRunner timeout behavior."""

import pytest

from cog.core.errors import RunnerTimeoutError
from cog.runners.claude_cli import ClaudeCliRunner
from cog.runners.sandbox import NullSandbox
from tests.test_runners.helpers import hanging_proc, patch_exec


async def test_timeout_raises_runner_timeout_error(monkeypatch):
    monkeypatch.setenv("COG_RUNNER_TIMEOUT_SECONDS", "0.05")
    proc = hanging_proc()
    runner = ClaudeCliRunner(NullSandbox())
    with patch_exec(proc):
        with pytest.raises(RunnerTimeoutError):
            async for _ in runner.stream("hello", model="claude-sonnet-4-5"):
                pass


async def test_timeout_terminates_process(monkeypatch):
    monkeypatch.setenv("COG_RUNNER_TIMEOUT_SECONDS", "0.05")
    proc = hanging_proc()
    runner = ClaudeCliRunner(NullSandbox())
    with patch_exec(proc):
        with pytest.raises(RunnerTimeoutError):
            async for _ in runner.stream("hello", model="claude-sonnet-4-5"):
                pass
    assert proc.terminated or proc.killed


async def test_timeout_error_message_includes_duration(monkeypatch):
    monkeypatch.setenv("COG_RUNNER_TIMEOUT_SECONDS", "0.05")
    proc = hanging_proc()
    runner = ClaudeCliRunner(NullSandbox())
    with patch_exec(proc):
        with pytest.raises(RunnerTimeoutError, match="0.05"):
            async for _ in runner.stream("hello", model="claude-sonnet-4-5"):
                pass
