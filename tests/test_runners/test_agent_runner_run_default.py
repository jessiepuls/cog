"""Tests for AgentRunner.run() default implementation draining stream()."""

from cog.core.runner import RunResult
from cog.runners.claude_cli import ClaudeCliRunner
from cog.runners.sandbox import NullSandbox
from tests.test_runners.helpers import fixture_proc, patch_exec


async def test_run_returns_run_result():
    proc = fixture_proc("happy.jsonl")
    runner = ClaudeCliRunner(NullSandbox())
    with patch_exec(proc):
        result = await runner.run("hello", model="claude-sonnet-4-5")
    assert isinstance(result, RunResult)


async def test_run_returns_final_message():
    proc = fixture_proc("happy.jsonl")
    runner = ClaudeCliRunner(NullSandbox())
    with patch_exec(proc):
        result = await runner.run("hello", model="claude-sonnet-4-5")
    assert result.final_message == "The command ran successfully."


async def test_run_returns_cost():
    proc = fixture_proc("happy.jsonl")
    runner = ClaudeCliRunner(NullSandbox())
    with patch_exec(proc):
        result = await runner.run("hello", model="claude-sonnet-4-5")
    assert result.total_cost_usd > 0


async def test_run_drains_stream_fully():
    """run() must consume the entire stream, not just stop at the first ResultEvent."""
    proc = fixture_proc("happy.jsonl")
    runner = ClaudeCliRunner(NullSandbox())
    with patch_exec(proc):
        result = await runner.run("hello", model="claude-sonnet-4-5")
    # If stream wasn't fully drained, the proc wouldn't be fully consumed
    assert result.exit_status == 0


async def test_run_nonzero_exit_status():
    proc = fixture_proc("nonzero_exit.jsonl")
    runner = ClaudeCliRunner(NullSandbox())
    with patch_exec(proc):
        result = await runner.run("hello", model="claude-sonnet-4-5")
    assert result.exit_status == 1
