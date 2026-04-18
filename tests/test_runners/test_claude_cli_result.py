"""Tests for ClaudeCliRunner RunResult field population."""

import pytest

from cog.core.runner import ResultEvent
from cog.runners.claude_cli import ClaudeCliRunner
from cog.runners.sandbox import NullSandbox
from tests.test_runners.helpers import fixture_proc, patch_exec


async def _get_result(fixture: str, returncode: int = 0):
    proc = fixture_proc(fixture, returncode)
    runner = ClaudeCliRunner(NullSandbox())
    with patch_exec(proc):
        events = [e async for e in runner.stream("hello", model="claude-sonnet-4-5")]
    result_event = events[-1]
    assert isinstance(result_event, ResultEvent)
    return result_event.result


async def test_happy_total_cost():
    result = await _get_result("happy.jsonl")
    assert result.total_cost_usd == pytest.approx(0.0087)


async def test_happy_exit_status_zero():
    result = await _get_result("happy.jsonl")
    assert result.exit_status == 0


async def test_happy_final_message():
    result = await _get_result("happy.jsonl")
    assert result.final_message == "The command ran successfully."


async def test_happy_stream_json_path_exists():
    result = await _get_result("happy.jsonl")
    assert result.stream_json_path.exists()


async def test_happy_stream_json_path_contains_content():
    result = await _get_result("happy.jsonl")
    content = result.stream_json_path.read_text()
    assert "assistant" in content


async def test_happy_duration_nonnegative():
    result = await _get_result("happy.jsonl")
    assert result.duration_seconds >= 0.0


async def test_nonzero_exit_status():
    result = await _get_result("nonzero_exit.jsonl")
    assert result.exit_status == 1


async def test_nonzero_cost():
    result = await _get_result("nonzero_exit.jsonl")
    assert result.total_cost_usd == pytest.approx(0.001)


async def test_tool_use_only_cost():
    result = await _get_result("tool_use_only.jsonl")
    assert result.total_cost_usd == pytest.approx(0.0042)


async def test_tool_use_only_final_message_empty():
    result = await _get_result("tool_use_only.jsonl")
    assert result.final_message == ""


async def test_no_result_record_falls_back_to_returncode():
    """When the stream has no result record, exit_status comes from proc.returncode."""
    result = await _get_result("no_result_record.jsonl", returncode=2)
    assert result.exit_status == 2


async def test_no_result_record_cost_is_zero():
    """When the stream has no result record, cost defaults to 0."""
    result = await _get_result("no_result_record.jsonl")
    assert result.total_cost_usd == 0.0


async def test_no_result_record_final_message_from_last_text():
    """When the stream has no result record, final_message is the last assistant text."""
    result = await _get_result("no_result_record.jsonl")
    assert result.final_message == "Partial output before crash."
