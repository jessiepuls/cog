"""Tests for ReviewScreen Textual pilot."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from cog.workflows.refine import ReviewDecision, ReviewOutcome


def _make_screen(
    tmp_path: Path,
    *,
    original_title: str = "Original Title",
    original_body: str = "Original body",
    proposed_title: str = "Proposed Title",
    proposed_body: str = "Proposed body",
):
    from cog.ui.screens.review import ReviewScreen

    return ReviewScreen(
        original_title=original_title,
        original_body=original_body,
        proposed_title=proposed_title,
        proposed_body=proposed_body,
        tmp_dir=tmp_path,
    )


@pytest.mark.asyncio
async def test_review_screen_renders_before_and_after(tmp_path):
    from textual.app import App

    screen = _make_screen(
        tmp_path,
        original_body="before content",
        proposed_body="after content",
    )

    class _App(App):
        async def on_mount(self) -> None:
            await self.push_screen(screen)

    app = _App()
    async with app.run_test() as pilot:
        await pilot.pause()
        assert str(screen.query_one("#original-body").renderable) == "before content"
        assert str(screen.query_one("#proposed-body").renderable) == "after content"


@pytest.mark.asyncio
async def test_review_screen_shows_title_change_strip(tmp_path):
    from textual.app import App

    screen = _make_screen(
        tmp_path,
        original_title="Old title",
        proposed_title="New title",
    )

    class _App(App):
        async def on_mount(self) -> None:
            await self.push_screen(screen)

    app = _App()
    async with app.run_test() as pilot:
        await pilot.pause()
        strip_text = str(screen.query_one("#review-header-strip").renderable)
        assert "Old title" in strip_text
        assert "New title" in strip_text


@pytest.mark.asyncio
async def test_review_screen_accept_key_dismisses_with_accept(tmp_path):
    from textual.app import App

    screen = _make_screen(tmp_path, proposed_body="accepted body", proposed_title="T")
    results: list[ReviewOutcome] = []

    class _App(App):
        def on_mount(self) -> None:
            async def _run():
                result = await self.push_screen_wait(screen)
                results.append(result)

            self.run_worker(_run())

    app = _App()
    async with app.run_test() as pilot:
        await pilot.press("a")
        await pilot.pause()

    assert results
    assert results[0].decision == ReviewDecision.ACCEPT
    assert results[0].final_body == "accepted body"


@pytest.mark.asyncio
async def test_review_screen_abandon_key_dismisses_with_abandon(tmp_path):
    from textual.app import App

    screen = _make_screen(tmp_path)
    results: list[ReviewOutcome] = []

    class _App(App):
        def on_mount(self) -> None:
            async def _run():
                result = await self.push_screen_wait(screen)
                results.append(result)

            self.run_worker(_run())

    app = _App()
    async with app.run_test() as pilot:
        await pilot.press("q")
        await pilot.pause()

    assert results
    assert results[0].decision == ReviewDecision.ABANDON


@pytest.mark.asyncio
async def test_review_screen_edit_invokes_editor_and_replaces_body(tmp_path):
    from textual.app import App

    screen = _make_screen(tmp_path, proposed_body="original proposed")
    results: list[ReviewOutcome] = []

    async def fake_suspend_and_edit(app, text, dir_, suffix=".md"):
        return "edited body"

    class _App(App):
        def on_mount(self) -> None:
            async def _run():
                result = await self.push_screen_wait(screen)
                results.append(result)

            self.run_worker(_run())

    app = _App()
    with patch("cog.ui.screens.review.suspend_and_edit", new=fake_suspend_and_edit):
        async with app.run_test() as pilot:
            await pilot.press("e")
            await pilot.pause()
            await pilot.press("a")
            await pilot.pause()

    assert results
    assert results[0].final_body == "edited body"


@pytest.mark.asyncio
async def test_review_screen_edit_exit_without_save_returns_to_prompt_with_original(tmp_path):
    from textual.app import App

    screen = _make_screen(tmp_path, proposed_body="original proposed")
    results: list[ReviewOutcome] = []

    async def fake_suspend_and_edit(app, text, dir_, suffix=".md"):
        return None  # no save

    class _App(App):
        def on_mount(self) -> None:
            async def _run():
                result = await self.push_screen_wait(screen)
                results.append(result)

            self.run_worker(_run())

    app = _App()
    with patch("cog.ui.screens.review.suspend_and_edit", new=fake_suspend_and_edit):
        async with app.run_test() as pilot:
            await pilot.press("e")
            await pilot.pause()
            await pilot.press("a")
            await pilot.pause()

    assert results
    assert results[0].final_body == "original proposed"


@pytest.mark.asyncio
async def test_review_screen_edit_then_accept_uses_edited_body(tmp_path):
    from textual.app import App

    screen = _make_screen(tmp_path, proposed_body="before edit")
    results: list[ReviewOutcome] = []

    async def fake_suspend_and_edit(app, text, dir_, suffix=".md"):
        return "after edit"

    class _App(App):
        def on_mount(self) -> None:
            async def _run():
                result = await self.push_screen_wait(screen)
                results.append(result)

            self.run_worker(_run())

    app = _App()
    with patch("cog.ui.screens.review.suspend_and_edit", new=fake_suspend_and_edit):
        async with app.run_test() as pilot:
            await pilot.press("e")
            await pilot.pause()
            await pilot.press("a")
            await pilot.pause()

    assert results[0].final_body == "after edit"


@pytest.mark.asyncio
async def test_review_screen_narrow_terminal_stacks_vertically(tmp_path):
    from textual.app import App

    screen = _make_screen(tmp_path)

    class _App(App):
        async def on_mount(self) -> None:
            await self.push_screen(screen)

    app = _App()
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        original = screen.query_one("#original-pane")
        assert "vertical" in original.classes
