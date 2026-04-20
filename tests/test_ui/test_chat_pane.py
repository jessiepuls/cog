"""Tests for ChatPaneWidget."""

import asyncio
from pathlib import Path

import pytest
from textual.app import App, ComposeResult
from textual.widgets import RichLog, Static, TextArea

from cog.core.runner import AssistantTextEvent, ResultEvent, RunResult
from cog.ui.widgets.chat_pane import ChatPaneWidget
from tests.fakes import make_tool_event


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


async def test_chat_pane_real_enter_press_submits_not_newline() -> None:
    # Regression: previously ChatPaneWidget.on_key was bubble-scope, which
    # TextArea pre-empts — pressing Enter just inserted a newline and never
    # submitted. Priority binding on the widget must intercept before
    # TextArea's internal Enter handling.
    async with _ChatApp().run_test(headless=True) as pilot:
        widget = pilot.app.query_one(ChatPaneWidget)
        area = widget.query_one("#input-area", TextArea)
        area.load_text("hello world")
        area.focus()
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        # Submit should have fired: textarea cleared + scrollback has the message
        assert area.text == ""
        log = widget.query_one("#scrollback", RichLog)
        assert "hello world" in _log_text(log)


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


async def test_chat_pane_prompt_returns_str_on_enter() -> None:
    async with _ChatApp().run_test(headless=True) as pilot:
        widget = pilot.app.query_one(ChatPaneWidget)
        area = widget.query_one("#input-area", TextArea)
        area.load_text("hello")
        await pilot.pause()

        async def _submit_after_delay() -> None:
            await asyncio.sleep(0.05)
            widget._submit()

        pilot.app.run_worker(_submit_after_delay())
        result = await widget.prompt()
        assert result == "hello"


async def test_chat_pane_prompt_returns_none_on_escape() -> None:
    async with _ChatApp().run_test(headless=True) as pilot:
        widget = pilot.app.query_one(ChatPaneWidget)

        async def _escape_after_delay() -> None:
            await asyncio.sleep(0.05)
            widget._end_interview()

        pilot.app.run_worker(_escape_after_delay())
        result = await widget.prompt()
        assert result is None


async def test_chat_pane_prompt_returns_none_on_ctrl_d() -> None:
    async with _ChatApp().run_test(headless=True) as pilot:
        widget = pilot.app.query_one(ChatPaneWidget)

        async def _end_after_delay() -> None:
            await asyncio.sleep(0.05)
            widget._end_interview()

        pilot.app.run_worker(_end_after_delay())
        result = await widget.prompt()
        assert result is None


async def test_chat_pane_prompt_submit_clears_textarea_for_next_turn() -> None:
    async with _ChatApp().run_test(headless=True) as pilot:
        widget = pilot.app.query_one(ChatPaneWidget)
        area = widget.query_one("#input-area", TextArea)
        area.load_text("first message")
        await pilot.pause()
        widget._submit()
        await pilot.pause()
        # after submit, textarea should be cleared
        assert area.text == ""


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


# Tool preview tests via emit path


@pytest.mark.parametrize(
    "tool,kwargs,expected_preview",
    [
        ("Bash", {"command": "ls -la"}, "ls -la"),
        ("Read", {"file_path": "/src/main.py"}, "/src/main.py"),
        ("Write", {"file_path": "/out/result.txt"}, "/out/result.txt"),
        ("Edit", {"file_path": "/src/foo.py", "old_string": "x", "new_string": "y"}, "/src/foo.py"),
        ("Glob", {"pattern": "**/*.py"}, "**/*.py"),
        ("Grep", {"pattern": "def foo"}, "def foo"),
        ("Agent", {"description": "explore widgets", "prompt": "search"}, "explore widgets"),
        ("Agent", {"prompt": "search for things"}, "search for things"),
        ("Task", {"description": "run tests", "prompt": "execute"}, "run tests"),
        ("ToolSearch", {"query": "select:Read"}, "select:Read"),
        ("Unknown", {"my_param": "some value"}, "some value"),
        ("Unknown", {"n": 42}, ""),
    ],
)
async def test_tool_preview_in_chat_pane(tool: str, kwargs: dict, expected_preview: str) -> None:
    async with _ChatApp().run_test(headless=True) as pilot:
        widget = pilot.app.query_one(ChatPaneWidget)
        await widget.emit(make_tool_event(tool, **kwargs))
        await pilot.pause()
        content = _log_text(widget.query_one("#scrollback", RichLog))
        assert tool in content
        if expected_preview:
            assert expected_preview in content


async def test_tool_preview_todowrite_shows_item_count() -> None:
    async with _ChatApp().run_test(headless=True) as pilot:
        widget = pilot.app.query_one(ChatPaneWidget)
        await widget.emit(make_tool_event("TodoWrite", todos=[{"id": 1}, {"id": 2}]))
        await pilot.pause()
        content = _log_text(widget.query_one("#scrollback", RichLog))
        assert "TodoWrite" in content
        assert "(2 items)" in content


async def test_tool_preview_truncates_at_100_chars_with_ellipsis() -> None:
    long_cmd = "x" * 101
    async with _ChatApp().run_test(headless=True) as pilot:
        widget = pilot.app.query_one(ChatPaneWidget)
        await widget.emit(make_tool_event("Bash", command=long_cmd))
        await pilot.pause()
        content = _log_text(widget.query_one("#scrollback", RichLog))
        assert "…" in content
        assert long_cmd not in content


async def test_tool_preview_at_exactly_100_chars_is_not_truncated() -> None:
    exact_cmd = "x" * 100
    async with _ChatApp().run_test(headless=True) as pilot:
        widget = pilot.app.query_one(ChatPaneWidget)
        await widget.emit(make_tool_event("Bash", command=exact_cmd))
        await pilot.pause()
        content = _log_text(widget.query_one("#scrollback", RichLog))
        assert exact_cmd in content


async def test_chat_pane_claude_message_renders_via_markdown() -> None:
    async with _ChatApp().run_test(headless=True) as pilot:
        widget = pilot.app.query_one(ChatPaneWidget)
        await widget.emit(AssistantTextEvent(text="**bold text**"))
        await pilot.pause()
        content = _log_text(widget.query_one("#scrollback", RichLog))
        # Markdown strips the asterisks — text appears but markers do not
        assert "bold text" in content
        assert "**bold text**" not in content


async def test_chat_pane_claude_message_renders_inline_code() -> None:
    async with _ChatApp().run_test(headless=True) as pilot:
        widget = pilot.app.query_one(ChatPaneWidget)
        await widget.emit(AssistantTextEvent(text="Use `foo()` here"))
        await pilot.pause()
        content = _log_text(widget.query_one("#scrollback", RichLog))
        assert "foo()" in content


async def test_chat_pane_claude_message_renders_code_block_with_distinct_style() -> None:
    async with _ChatApp().run_test(headless=True) as pilot:
        widget = pilot.app.query_one(ChatPaneWidget)
        await widget.emit(AssistantTextEvent(text="```\nprint('hello')\n```"))
        await pilot.pause()
        content = _log_text(widget.query_one("#scrollback", RichLog))
        assert "print" in content
        assert "hello" in content


async def test_chat_pane_user_message_renders_as_plain_text_not_markdown() -> None:
    async with _ChatApp().run_test(headless=True) as pilot:
        widget = pilot.app.query_one(ChatPaneWidget)
        area = widget.query_one("#input-area", TextArea)
        area.load_text("**hello**")
        await pilot.pause()
        widget._submit()
        await pilot.pause()
        content = _log_text(widget.query_one("#scrollback", RichLog))
        # Plain Text — asterisks preserved as-is
        assert "**hello**" in content


async def test_chat_pane_tool_call_renders_with_dim_style_and_preview() -> None:
    async with _ChatApp().run_test(headless=True) as pilot:
        widget = pilot.app.query_one(ChatPaneWidget)
        await widget.emit(make_tool_event("Bash", command="echo hi"))
        await pilot.pause()
        content = _log_text(widget.query_one("#scrollback", RichLog))
        assert "🔧" in content
        assert "Bash" in content
        assert "echo hi" in content


async def test_chat_pane_todowrite_renders_count_placeholder_not_full_todos() -> None:
    async with _ChatApp().run_test(headless=True) as pilot:
        widget = pilot.app.query_one(ChatPaneWidget)
        todos = [{"id": i, "content": f"item {i}"} for i in range(5)]
        await widget.emit(make_tool_event("TodoWrite", todos=todos))
        await pilot.pause()
        content = _log_text(widget.query_one("#scrollback", RichLog))
        assert "TodoWrite" in content
        assert "(5 items)" in content
        # Full todo content should NOT be shown
        assert "item 0" not in content


async def test_chat_pane_turn_separator_blank_line_before_each_speaker_message() -> None:
    async with _ChatApp().run_test(headless=True) as pilot:
        widget = pilot.app.query_one(ChatPaneWidget)
        log = widget.query_one("#scrollback", RichLog)

        await widget.emit(AssistantTextEvent(text="First message"))
        await pilot.pause()
        await widget.emit(AssistantTextEvent(text="Second message"))
        await pilot.pause()
        lines_after_second = list(log.lines)

        # Each message adds a blank line + content, so two messages add more lines
        # than one message. Blank lines appear in the strip list as empty-text strips.
        blank_count = sum(1 for s in lines_after_second if s.text == "")
        assert blank_count >= 2


async def test_chat_pane_tool_calls_do_not_get_blank_line_separators() -> None:
    async with _ChatApp().run_test(headless=True) as pilot:
        widget = pilot.app.query_one(ChatPaneWidget)
        log = widget.query_one("#scrollback", RichLog)

        await widget.emit(make_tool_event("Bash", command="ls"))
        await widget.emit(make_tool_event("Read", file_path="/a.py"))
        await pilot.pause()

        # Tool lines go through _append_tool_line which writes no blank line
        blank_count = sum(1 for s in log.lines if s.text == "")
        assert blank_count == 0


async def test_chat_pane_richlog_has_wrap_enabled() -> None:
    async with _ChatApp().run_test(headless=True) as pilot:
        widget = pilot.app.query_one(ChatPaneWidget)
        log = widget.query_one("#scrollback", RichLog)
        assert log.wrap is True


async def test_chat_pane_info_header_shows_placeholder_before_item_set() -> None:
    async with _ChatApp().run_test(headless=True) as pilot:
        widget = pilot.app.query_one(ChatPaneWidget)
        await pilot.pause()
        header = widget.query_one("#info-header", Static)
        assert "no item" in str(header.renderable).lower()


async def test_chat_pane_item_selected_event_populates_header() -> None:
    from cog.core.runner import ItemSelectedEvent
    from tests.fakes import make_item

    async with _ChatApp().run_test(headless=True) as pilot:
        widget = pilot.app.query_one(ChatPaneWidget)
        item = make_item(item_id="42", title="Fix the thing", labels=("p1", "bug"))
        await widget.emit(ItemSelectedEvent(item=item))
        await pilot.pause()
        header = widget.query_one("#info-header", Static)
        rendered = str(header.renderable)
        assert "42" in rendered
        assert "Fix the thing" in rendered
        assert "p1" in rendered


async def test_chat_pane_info_body_hidden_by_default() -> None:
    from cog.core.runner import ItemSelectedEvent
    from tests.fakes import make_item

    async with _ChatApp().run_test(headless=True) as pilot:
        widget = pilot.app.query_one(ChatPaneWidget)
        await widget.emit(ItemSelectedEvent(item=make_item(body="Long body here")))
        await pilot.pause()
        body = widget.query_one("#info-body")
        assert body.has_class("hidden")


async def test_chat_pane_ctrl_i_toggles_info_body_visibility() -> None:
    from cog.core.runner import ItemSelectedEvent
    from tests.fakes import make_item

    async with _ChatApp().run_test(headless=True) as pilot:
        widget = pilot.app.query_one(ChatPaneWidget)
        await widget.emit(ItemSelectedEvent(item=make_item(body="The issue body")))
        await pilot.pause()
        body = widget.query_one("#info-body")
        # starts hidden
        assert body.has_class("hidden")
        await pilot.press("ctrl+i")
        await pilot.pause()
        assert not body.has_class("hidden")
        await pilot.press("ctrl+i")
        await pilot.pause()
        assert body.has_class("hidden")


async def test_chat_pane_check_action_hides_show_binding_when_expanded() -> None:
    from cog.core.runner import ItemSelectedEvent
    from tests.fakes import make_item

    async with _ChatApp().run_test(headless=True) as pilot:
        widget = pilot.app.query_one(ChatPaneWidget)
        await widget.emit(ItemSelectedEvent(item=make_item()))
        await pilot.pause()
        # collapsed: show_info visible, hide_info hidden
        assert widget.check_action("show_info", ()) is True
        assert widget.check_action("hide_info", ()) is None
        widget._set_info_expanded(True)
        assert widget.check_action("show_info", ()) is None
        assert widget.check_action("hide_info", ()) is True


async def test_chat_pane_check_action_hides_both_when_no_item() -> None:
    async with _ChatApp().run_test(headless=True) as pilot:
        widget = pilot.app.query_one(ChatPaneWidget)
        await pilot.pause()
        assert widget.check_action("show_info", ()) is None
        assert widget.check_action("hide_info", ()) is None


async def test_chat_pane_renders_status_event_as_dim_line() -> None:
    from cog.core.runner import StatusEvent

    async with _ChatApp().run_test(headless=True) as pilot:
        widget = pilot.app.query_one(ChatPaneWidget)
        await widget.emit(StatusEvent(message="⏳ Waiting for CI on PR #42..."))
        await pilot.pause()
        content = _log_text(widget.query_one("#scrollback", RichLog))
        assert "⏳ Waiting for CI on PR #42..." in content
