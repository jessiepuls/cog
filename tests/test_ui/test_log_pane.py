"""Tests for LogPaneWidget."""

from pathlib import Path

from textual.app import App, ComposeResult
from textual.widgets import RichLog

from cog.core.runner import AssistantTextEvent, ResultEvent, RunResult, ToolUseEvent
from cog.ui.widgets.log_pane import LogPaneWidget


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
