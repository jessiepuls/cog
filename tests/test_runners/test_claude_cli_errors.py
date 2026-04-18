"""Tests for ClaudeCliRunner error cases."""

import pytest

from cog.core.errors import StreamJsonParseError
from cog.core.runner import AssistantTextEvent
from cog.runners.claude_cli import ClaudeCliRunner
from cog.runners.sandbox import NullSandbox
from tests.test_runners.helpers import fixture_proc, patch_exec


async def test_malformed_json_raises_stream_parse_error():
    proc = fixture_proc("malformed.jsonl")
    runner = ClaudeCliRunner(NullSandbox())
    with patch_exec(proc):
        with pytest.raises(StreamJsonParseError):
            async for _ in runner.stream("hello", model="claude-sonnet-4-5"):
                pass


async def test_malformed_events_before_error_are_yielded():
    proc = fixture_proc("malformed.jsonl")
    runner = ClaudeCliRunner(NullSandbox())
    events = []
    with patch_exec(proc):
        with pytest.raises(StreamJsonParseError):
            async for event in runner.stream("hello", model="claude-sonnet-4-5"):
                events.append(event)
    # The first valid assistant text event is emitted before the error
    assert len(events) == 1
    assert isinstance(events[0], AssistantTextEvent)
    assert events[0].text == "Starting..."


async def test_malformed_error_message_contains_bad_line():
    proc = fixture_proc("malformed.jsonl")
    runner = ClaudeCliRunner(NullSandbox())
    with patch_exec(proc):
        with pytest.raises(StreamJsonParseError, match="bad JSON line"):
            async for _ in runner.stream("hello", model="claude-sonnet-4-5"):
                pass
