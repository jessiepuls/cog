"""Tests for LogPaneWidget."""

from pathlib import Path

import pytest
from textual.app import App, ComposeResult
from textual.widgets import RichLog

from cog.core.runner import AssistantTextEvent, ResultEvent, RunResult, ToolUseEvent
from cog.ui.widgets.log_pane import LogPaneWidget
from tests.fakes import make_tool_event


def _log_text(log: RichLog) -> str:
    return "".join(s.text for s in log.lines)


class _LogApp(App):
    def compose(self) -> ComposeResult:
        yield LogPaneWidget()


async def test_log_pane_renders_assistant_text() -> None:
    async with _LogApp().run_test(headless=True) as pilot:
        widget = pilot.app.query_one(LogPaneWidget)
        await widget.emit(AssistantTextEvent(text="hello world"))
        await pilot.pause()
        log = widget.query_one("#log", RichLog)
        assert "hello world" in _log_text(log)


async def test_log_pane_renders_tool_use_with_input_preview() -> None:
    async with _LogApp().run_test(headless=True) as pilot:
        widget = pilot.app.query_one(LogPaneWidget)
        await widget.emit(ToolUseEvent(tool="bash", input={"command": "ls -la"}))
        await pilot.pause()
        content = _log_text(widget.query_one("#log", RichLog))
        assert "bash" in content
        assert "ls -la" in content


async def test_log_pane_renders_result_event_as_divider() -> None:
    result = RunResult(
        final_message="done",
        total_cost_usd=0.042,
        exit_status=0,
        stream_json_path=Path("/dev/null"),
        duration_seconds=1.0,
    )
    async with _LogApp().run_test(headless=True) as pilot:
        widget = pilot.app.query_one(LogPaneWidget)
        await widget.emit(ResultEvent(result=result))
        await pilot.pause()
        content = _log_text(widget.query_one("#log", RichLog))
        assert "0.042" in content


async def test_log_pane_auto_scrolls_at_bottom() -> None:
    async with _LogApp().run_test(headless=True) as pilot:
        widget = pilot.app.query_one(LogPaneWidget)
        assert widget._pinned is True  # default: stick to bottom
        await widget.emit(AssistantTextEvent(text="line"))
        await pilot.pause()
        # _pinned should remain True when nothing has scrolled up
        assert widget._pinned is True


async def test_log_pane_preserves_scroll_when_user_scrolled_up() -> None:
    async with _LogApp().run_test(headless=True) as pilot:
        widget = pilot.app.query_one(LogPaneWidget)
        widget._pinned = False  # simulate: user has scrolled up

        log = widget.query_one("#log", RichLog)
        y_before = log.scroll_y

        await widget.emit(AssistantTextEvent(text="new content"))
        await pilot.pause()

        # scroll_y unchanged because _pinned is False → no scroll_end called
        assert log.scroll_y == y_before


async def test_log_pane_assistant_message_renders_via_markdown() -> None:
    async with _LogApp().run_test(headless=True) as pilot:
        widget = pilot.app.query_one(LogPaneWidget)
        await widget.emit(AssistantTextEvent(text="**bold text**"))
        await pilot.pause()
        content = _log_text(widget.query_one("#log", RichLog))
        assert "bold text" in content
        assert "**bold text**" not in content


async def test_log_pane_assistant_message_has_no_claude_prefix() -> None:
    async with _LogApp().run_test(headless=True) as pilot:
        widget = pilot.app.query_one(LogPaneWidget)
        await widget.emit(AssistantTextEvent(text="some response"))
        await pilot.pause()
        content = _log_text(widget.query_one("#log", RichLog))
        assert "some response" in content
        assert "Claude:" not in content


async def test_log_pane_tool_call_renders_with_preview() -> None:
    async with _LogApp().run_test(headless=True) as pilot:
        widget = pilot.app.query_one(LogPaneWidget)
        await widget.emit(make_tool_event("Read", file_path="/src/main.py"))
        await pilot.pause()
        content = _log_text(widget.query_one("#log", RichLog))
        assert "🔧" in content
        assert "Read" in content
        assert "/src/main.py" in content


async def test_log_pane_richlog_has_wrap_enabled() -> None:
    async with _LogApp().run_test(headless=True) as pilot:
        widget = pilot.app.query_one(LogPaneWidget)
        log = widget.query_one("#log", RichLog)
        assert log.wrap is True


async def test_log_pane_todowrite_renders_count_placeholder() -> None:
    async with _LogApp().run_test(headless=True) as pilot:
        widget = pilot.app.query_one(LogPaneWidget)
        await widget.emit(make_tool_event("TodoWrite", todos=[{"id": 1}, {"id": 2}, {"id": 3}]))
        await pilot.pause()
        content = _log_text(widget.query_one("#log", RichLog))
        assert "TodoWrite" in content
        assert "(3 items)" in content


async def test_log_pane_stage_separator_still_renders_on_result_event() -> None:
    result = RunResult(
        final_message="done",
        total_cost_usd=0.123,
        exit_status=0,
        stream_json_path=Path("/dev/null"),
        duration_seconds=5.0,
    )
    async with _LogApp().run_test(headless=True) as pilot:
        widget = pilot.app.query_one(LogPaneWidget)
        await widget.emit(ResultEvent(result=result))
        await pilot.pause()
        content = _log_text(widget.query_one("#log", RichLog))
        assert "stage complete" in content
        assert "0.123" in content


@pytest.mark.parametrize(
    "tool,kwargs,expected_in_output",
    [
        ("Bash", {"command": "echo hi"}, "echo hi"),
        ("Read", {"file_path": "/a.py"}, "/a.py"),
        ("Write", {"file_path": "/b.py"}, "/b.py"),
        ("Edit", {"file_path": "/c.py"}, "/c.py"),
        ("Glob", {"pattern": "*.py"}, "*.py"),
        ("Grep", {"pattern": "def foo"}, "def foo"),
        ("Agent", {"description": "desc", "prompt": "p"}, "desc"),
        ("Agent", {"prompt": "fallback"}, "fallback"),
        ("ToolSearch", {"query": "search"}, "search"),
        ("TodoWrite", {"todos": [1, 2]}, "(2 items)"),
    ],
)
async def test_log_pane_tool_preview_extraction_matches_chat_pane_for_all_tools(
    tool: str, kwargs: dict, expected_in_output: str
) -> None:
    async with _LogApp().run_test(headless=True) as pilot:
        widget = pilot.app.query_one(LogPaneWidget)
        await widget.emit(make_tool_event(tool, **kwargs))
        await pilot.pause()
        content = _log_text(widget.query_one("#log", RichLog))
        assert expected_in_output in content
