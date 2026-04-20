"""Tests for ChatView (#130)."""

from __future__ import annotations

from pathlib import Path

from textual.app import App, ComposeResult

from cog.ui.views.chat import ChatView, _Turn
from cog.ui.widgets.chat_pane import ChatPaneWidget


class _ChatApp(App):
    def __init__(self, project_dir: Path) -> None:
        super().__init__()
        self._project_dir = project_dir

    def compose(self) -> ComposeResult:
        yield ChatView(self._project_dir)


async def test_chat_view_mounts_with_chat_pane(tmp_path: Path) -> None:
    async with _ChatApp(tmp_path).run_test(headless=True) as pilot:
        await pilot.pause()
        view = pilot.app.query_one(ChatView)
        pane = view.query_one(ChatPaneWidget)
        assert pane is not None


async def test_chat_view_build_prompt_includes_preamble(tmp_path: Path) -> None:
    async with _ChatApp(tmp_path).run_test(headless=True) as pilot:
        await pilot.pause()
        view = pilot.app.query_one(ChatView)
        view._transcript.append(_Turn(role="user", content="hi"))
        prompt = view._build_prompt()
        assert "/work" in prompt
        assert "hi" in prompt


async def test_chat_view_build_prompt_includes_full_transcript(tmp_path: Path) -> None:
    async with _ChatApp(tmp_path).run_test(headless=True) as pilot:
        await pilot.pause()
        view = pilot.app.query_one(ChatView)
        view._transcript = [
            _Turn(role="user", content="what does X do"),
            _Turn(role="assistant", content="X is a widget"),
            _Turn(role="user", content="tell me more"),
        ]
        prompt = view._build_prompt()
        assert "what does X do" in prompt
        assert "X is a widget" in prompt
        assert "tell me more" in prompt


async def test_chat_view_clear_resets_transcript(tmp_path: Path) -> None:
    async with _ChatApp(tmp_path).run_test(headless=True) as pilot:
        await pilot.pause()
        view = pilot.app.query_one(ChatView)
        view._transcript = [
            _Turn(role="user", content="x"),
            _Turn(role="assistant", content="y"),
        ]
        view.action_clear_chat()
        assert view._transcript == []


async def test_chat_view_needs_attention_always_none(tmp_path: Path) -> None:
    async with _ChatApp(tmp_path).run_test(headless=True) as pilot:
        await pilot.pause()
        view = pilot.app.query_one(ChatView)
        assert view.needs_attention() is None


async def test_chat_view_busy_description_always_none(tmp_path: Path) -> None:
    # Chat doesn't flag the quit-confirm modal — users shouldn't have to
    # explicitly dismiss "you have a chat tab open" to exit.
    async with _ChatApp(tmp_path).run_test(headless=True) as pilot:
        await pilot.pause()
        view = pilot.app.query_one(ChatView)
        assert view.busy_description() is None


async def test_chat_view_focus_content_targets_text_area(tmp_path: Path) -> None:
    from textual.widgets import TextArea

    async with _ChatApp(tmp_path).run_test(headless=True) as pilot:
        await pilot.pause()
        view = pilot.app.query_one(ChatView)
        view.focus_content()
        await pilot.pause()
        focused = pilot.app.focused
        assert isinstance(focused, TextArea)
        assert focused.id == "input-area"


async def test_chat_view_posts_attention_when_response_arrives() -> None:
    # Regression: the dot didn't appear on chat when Claude finished while
    # the user was on another view. The view now posts ViewAttention after
    # each assistant turn completes.
    import asyncio
    import tempfile

    from textual.app import App

    from cog.ui.messages import ViewAttention

    class _App(App):
        def compose(self):
            yield ChatView(Path(tempfile.gettempdir()))

    async with _App().run_test(headless=True) as pilot:
        await pilot.pause()
        view = pilot.app.query_one(ChatView)
        captured: list[ViewAttention] = []
        original_post = view.post_message

        def _capture(msg):
            if isinstance(msg, ViewAttention):
                captured.append(msg)
            return original_post(msg)

        view.post_message = _capture  # type: ignore[method-assign]

        # Emulate what _chat_loop does after a successful turn.
        view._transcript.append(_Turn(role="assistant", content="hi"))
        view.post_message(ViewAttention("chat", reason="response ready"))
        await asyncio.sleep(0)  # let the message post

        assert any(m.view_id == "chat" for m in captured)


async def test_chat_view_preamble_loads_from_package() -> None:
    from cog.ui.views.chat import _load_preamble

    text = _load_preamble()
    assert "/work" in text
    assert "conversational" in text.lower()
