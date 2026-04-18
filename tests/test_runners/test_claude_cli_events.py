"""Tests for ClaudeCliRunner event emission."""

from cog.core.runner import AssistantTextEvent, ResultEvent, ToolUseEvent
from cog.runners.claude_cli import ClaudeCliRunner
from cog.runners.sandbox import NullSandbox
from tests.test_runners.helpers import fixture_proc, patch_exec


async def _collect(fixture: str, returncode: int = 0) -> list:
    proc = fixture_proc(fixture, returncode)
    runner = ClaudeCliRunner(NullSandbox())
    with patch_exec(proc):
        return [e async for e in runner.stream("hello", model="claude-sonnet-4-5")]


async def test_happy_path_event_types():
    events = await _collect("happy.jsonl")
    types = [type(e) for e in events]
    assert AssistantTextEvent in types
    assert ToolUseEvent in types
    assert ResultEvent in types


async def test_happy_path_text_content():
    events = await _collect("happy.jsonl")
    texts = [e.text for e in events if isinstance(e, AssistantTextEvent)]
    assert texts == ["I will help you with that.", "The command ran successfully."]


async def test_happy_path_tool_use_content():
    events = await _collect("happy.jsonl")
    tools = [e for e in events if isinstance(e, ToolUseEvent)]
    assert len(tools) == 1
    assert tools[0].tool == "Bash"
    assert tools[0].input == {"command": "echo hello"}


async def test_result_event_is_last():
    events = await _collect("happy.jsonl")
    assert isinstance(events[-1], ResultEvent)


async def test_tool_use_only_no_text_events():
    events = await _collect("tool_use_only.jsonl")
    text_events = [e for e in events if isinstance(e, AssistantTextEvent)]
    assert text_events == []


async def test_tool_use_only_has_tool_events():
    events = await _collect("tool_use_only.jsonl")
    tools = [e for e in events if isinstance(e, ToolUseEvent)]
    assert len(tools) == 2
    assert tools[0].tool == "Read"
    assert tools[1].tool == "Grep"


async def test_tool_use_only_result_is_last():
    events = await _collect("tool_use_only.jsonl")
    assert isinstance(events[-1], ResultEvent)


async def test_unknown_events_dropped():
    events = await _collect("unknown_events.jsonl")
    # system and user records should be silently dropped
    non_result = [e for e in events if not isinstance(e, ResultEvent)]
    text_events = [e for e in non_result if isinstance(e, AssistantTextEvent)]
    assert len(text_events) == 1
    assert text_events[0].text == "Processing..."
    # result event still present
    assert isinstance(events[-1], ResultEvent)


async def test_unknown_events_no_extra_types():
    events = await _collect("unknown_events.jsonl")
    for e in events:
        assert isinstance(e, AssistantTextEvent | ToolUseEvent | ResultEvent)
