"""Tests for ChatPaneWidget."""

import asyncio
from pathlib import Path

from textual.app import App, ComposeResult
from textual.widgets import RichLog, Static, TextArea

from cog.core.runner import AssistantTextEvent, ResultEvent, RunResult
from cog.ui.widgets.chat_pane import ChatPaneWidget


def _log_text(log: RichLog) -> str:
    return "".join(s.text for s in log.lines)


class _ChatApp(App):
    def compose(self) -> ComposeResult:
        yield ChatPaneWidget()


async def test_chat_pane_renders_assistant_text_into_scrollback() -> None:
    async with _ChatApp().run_test(headless=True) as pilot:
        widget = pilot.app.query_one(ChatPaneWidget)
        await widget.emit(AssistantTextEvent(text="Hello there"))
        await pilot.pause()
        log = widget.query_one("#scrollback", RichLog)
        assert "Hello there" in _log_text(log)


async def test_chat_pane_prompt_returns_submitted_text() -> None:
    async with _ChatApp().run_test(headless=True) as pilot:
        widget = pilot.app.query_one(ChatPaneWidget)

        async def _resolve_after_delay() -> None:
            await asyncio.sleep(0.05)
            future = widget._ensure_future()
            if not future.done():
                future.set_result("my response")

        pilot.app.run_worker(_resolve_after_delay())
        result = await widget.prompt()
        assert result == "my response"


async def test_chat_pane_enter_submits() -> None:
    async with _ChatApp().run_test(headless=True) as pilot:
        widget = pilot.app.query_one(ChatPaneWidget)
        area = widget.query_one("#input-area", TextArea)
        # Load text directly, then trigger submit
        area.load_text("test message")
        await pilot.pause()
        widget._submit()
        await pilot.pause()
        log = widget.query_one("#scrollback", RichLog)
        assert "test message" in _log_text(log)


async def test_chat_pane_shift_enter_inserts_newline() -> None:
    async with _ChatApp().run_test(headless=True) as pilot:
        widget = pilot.app.query_one(ChatPaneWidget)
        area = widget.query_one("#input-area", TextArea)
        area.load_text("line1")
        await pilot.pause()
        # shift+enter inserts a newline (not captured by on_key which only stops plain enter)
        await pilot.press("shift+enter")
        await pilot.pause()
        # Future not resolved — shift+enter did NOT submit
        future = widget._ensure_future()
        assert not future.done()


async def test_chat_pane_thinking_indicator_during_emit() -> None:
    async with _ChatApp().run_test(headless=True) as pilot:
        widget = pilot.app.query_one(ChatPaneWidget)
        thinking = widget.query_one("#thinking", Static)

        # Initially hidden
        assert thinking.display is False

        result = RunResult(
            final_message="done",
            total_cost_usd=0.0,
            exit_status=0,
            stream_json_path=Path("/dev/null"),
            duration_seconds=0.0,
        )
        await widget.emit(ResultEvent(result=result))
        await pilot.pause()
        # _hide_thinking_indicator called → still hidden
        assert thinking.display is False
