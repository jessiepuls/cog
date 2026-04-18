"""Tests for ClaudeCliRunner sandbox integration."""

from cog.runners.claude_cli import ClaudeCliRunner
from tests.test_runners.helpers import RecordingSandbox, fixture_proc, patch_exec


async def _run_with_sandbox(sandbox: RecordingSandbox) -> None:
    proc = fixture_proc("happy.jsonl")
    runner = ClaudeCliRunner(sandbox)
    with patch_exec(proc):
        async for _ in runner.stream("hello", model="claude-sonnet-4-5"):
            pass


async def test_prepare_called_before_subprocess():
    sandbox = RecordingSandbox()
    await _run_with_sandbox(sandbox)
    assert sandbox.prepare_calls == 1


async def test_wrap_argv_called():
    sandbox = RecordingSandbox()
    await _run_with_sandbox(sandbox)
    assert len(sandbox.wrap_argv_args) == 1
    argv = sandbox.wrap_argv_args[0]
    assert argv[0] == "claude"
    assert "--model" in argv
    assert "claude-sonnet-4-5" in argv


async def test_wrap_argv_contains_required_flags():
    sandbox = RecordingSandbox()
    await _run_with_sandbox(sandbox)
    argv = sandbox.wrap_argv_args[0]
    assert "--print" in argv
    assert "--output-format" in argv
    assert "stream-json" in argv
    assert "--dangerously-skip-permissions" in argv


async def test_wrap_env_called():
    sandbox = RecordingSandbox()
    await _run_with_sandbox(sandbox)
    assert len(sandbox.wrap_env_args) == 1
    assert isinstance(sandbox.wrap_env_args[0], dict)


async def test_prepare_called_each_stream_invocation():
    sandbox = RecordingSandbox()
    proc1 = fixture_proc("happy.jsonl")
    proc2 = fixture_proc("happy.jsonl")
    runner = ClaudeCliRunner(sandbox)
    with patch_exec(proc1):
        async for _ in runner.stream("first", model="claude-sonnet-4-5"):
            pass
    with patch_exec(proc2):
        async for _ in runner.stream("second", model="claude-sonnet-4-5"):
            pass
    assert sandbox.prepare_calls == 2
