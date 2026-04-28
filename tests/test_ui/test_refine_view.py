"""Tests for RefineView (#124)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from textual.app import App, ComposeResult
from textual.widgets import ListView, Static

from cog.core.item import Item
from cog.core.tracker import IssueTracker
from cog.ui.views.refine import RefineView
from cog.workflows.refine import ReviewDecision


def _item(n: int) -> Item:
    return Item(
        tracker_id="gh",
        item_id=str(n),
        title=f"item {n}",
        body=f"body of #{n}",
        labels=(),
        comments=(),
        state="open",
        created_at=datetime(2026, 4, 20, tzinfo=UTC),
        updated_at=datetime(2026, 4, 20, tzinfo=UTC),
        url="",
    )


def _tracker_with(items: list[Item]) -> IssueTracker:
    t = AsyncMock(spec=IssueTracker)
    t.list_by_label = AsyncMock(return_value=items)
    return t  # type: ignore[return-value]


@pytest.fixture
def xdg_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setenv("XDG_STATE_HOME", str(state))
    return state


class _RefineApp(App):
    def __init__(self, project_dir: Path, tracker: IssueTracker) -> None:
        super().__init__()
        self._project_dir = project_dir
        self._tracker = tracker

    def compose(self) -> ComposeResult:
        yield RefineView(self._project_dir, self._tracker)


async def test_refine_view_mounts_in_idle_state_and_lists_queue(
    tmp_path: Path, xdg_state: Path
) -> None:
    tracker = _tracker_with([_item(1), _item(2), _item(3)])
    async with _RefineApp(tmp_path, tracker).run_test(headless=True) as pilot:
        await pilot.pause()
        view = pilot.app.query_one(RefineView)
        assert view._substate == "idle"
        queue = view.query_one("#refine-queue", ListView)
        assert len(queue.children) == 3


async def test_refine_view_idle_shows_empty_state_when_no_items(
    tmp_path: Path, xdg_state: Path
) -> None:
    tracker = _tracker_with([])
    async with _RefineApp(tmp_path, tracker).run_test(headless=True) as pilot:
        await pilot.pause()
        view = pilot.app.query_one(RefineView)
        status = view.query_one("#refine-status", Static)
        assert "empty" in str(status.renderable).lower()


async def test_refine_view_review_accept_resolves_future_with_accept(
    tmp_path: Path, xdg_state: Path
) -> None:
    # Drive the review provider directly — no need to run a full workflow.
    tracker = _tracker_with([])
    async with _RefineApp(tmp_path, tracker).run_test(headless=True) as pilot:
        await pilot.pause()
        view = pilot.app.query_one(RefineView)

        async def _press_accept_later() -> None:
            await asyncio.sleep(0.05)
            view.action_review_accept()

        pilot.app.run_worker(_press_accept_later())
        outcome = await view.review(
            original_title="orig",
            original_body="original",
            proposed_title="new",
            proposed_body="proposed",
            tmp_dir=tmp_path,
        )
        assert outcome.decision == ReviewDecision.ACCEPT
        assert outcome.final_title == "new"
        assert outcome.final_body == "proposed"


async def test_refine_view_review_abandon_resolves_future_with_abandon(
    tmp_path: Path, xdg_state: Path
) -> None:
    tracker = _tracker_with([])
    async with _RefineApp(tmp_path, tracker).run_test(headless=True) as pilot:
        await pilot.pause()
        view = pilot.app.query_one(RefineView)

        async def _press_abandon_later() -> None:
            await asyncio.sleep(0.05)
            view.action_review_abandon()

        pilot.app.run_worker(_press_abandon_later())
        outcome = await view.review(
            original_title="orig",
            original_body="original",
            proposed_title="new",
            proposed_body="proposed",
            tmp_dir=tmp_path,
        )
        assert outcome.decision == ReviewDecision.ABANDON


async def test_refine_view_review_switches_to_review_substate(
    tmp_path: Path, xdg_state: Path
) -> None:
    tracker = _tracker_with([])
    async with _RefineApp(tmp_path, tracker).run_test(headless=True) as pilot:
        await pilot.pause()
        view = pilot.app.query_one(RefineView)

        # Start the review; the future will be pending.
        async def _resolve_later() -> None:
            await asyncio.sleep(0.05)
            view.action_review_accept()

        pilot.app.run_worker(_resolve_later())

        # Before resolution, substate is "review"
        task = asyncio.create_task(
            view.review(
                original_title="a",
                original_body="a-body",
                proposed_title="b",
                proposed_body="b-body",
                tmp_dir=tmp_path,
            )
        )
        await pilot.pause()
        assert view._substate == "review"
        await task


async def test_refine_view_review_panes_populated_with_bodies(
    tmp_path: Path, xdg_state: Path
) -> None:
    tracker = _tracker_with([])
    async with _RefineApp(tmp_path, tracker).run_test(headless=True) as pilot:
        await pilot.pause()
        view = pilot.app.query_one(RefineView)

        async def _resolve_later() -> None:
            await asyncio.sleep(0.05)
            view.action_review_accept()

        pilot.app.run_worker(_resolve_later())
        task = asyncio.create_task(
            view.review(
                original_title="orig-title",
                original_body="ORIGINAL body content",
                proposed_title="new-title",
                proposed_body="PROPOSED body content",
                tmp_dir=tmp_path,
            )
        )
        await pilot.pause()
        orig = view.query_one("#review-original-body", Static)
        prop = view.query_one("#review-proposed-body", Static)
        # Markdown renderable: check the markup attribute for the original source.
        assert "ORIGINAL" in orig.renderable.markup  # type: ignore[attr-defined]
        assert "PROPOSED" in prop.renderable.markup  # type: ignore[attr-defined]
        await task


async def test_refine_view_check_action_hides_review_bindings_outside_review(
    tmp_path: Path, xdg_state: Path
) -> None:
    tracker = _tracker_with([])
    async with _RefineApp(tmp_path, tracker).run_test(headless=True) as pilot:
        await pilot.pause()
        view = pilot.app.query_one(RefineView)
        # Idle state — review bindings hidden
        assert view.check_action("review_accept", ()) is None
        assert view.check_action("review_abandon", ()) is None
        assert view.check_action("review_edit", ()) is None
        # Refresh is visible in idle
        assert view.check_action("refresh_queue", ()) is True


async def test_refine_view_busy_description_matches_substate(
    tmp_path: Path, xdg_state: Path
) -> None:
    tracker = _tracker_with([])
    async with _RefineApp(tmp_path, tracker).run_test(headless=True) as pilot:
        await pilot.pause()
        view = pilot.app.query_one(RefineView)
        # Idle → None
        assert view.busy_description() is None
        # Running → "interview"
        view._substate = "running"
        view._active_item = _item(42)
        assert view.busy_description() == "Refine interview on #42"
        # Review → "review pending"
        view._substate = "review"
        assert view.busy_description() == "Refine review pending on #42"


async def test_refine_view_needs_attention_when_chat_awaiting_reply(
    tmp_path: Path, xdg_state: Path
) -> None:
    import asyncio

    from cog.ui.widgets.chat_pane import ChatPaneWidget

    tracker = _tracker_with([])
    async with _RefineApp(tmp_path, tracker).run_test(headless=True) as pilot:
        await pilot.pause()
        view = pilot.app.query_one(RefineView)
        chat = ChatPaneWidget()
        await view.mount(chat)
        view._chat_pane = chat
        view._substate = "running"

        # No pending future — no attention
        chat._input_future = None
        assert view.needs_attention() is None

        # Create a pending future — attention
        loop = asyncio.get_running_loop()
        chat._input_future = loop.create_future()
        assert view.needs_attention() == "awaiting reply"

        # Resolve it — no attention
        chat._input_future.set_result("done")
        assert view.needs_attention() is None


async def test_refine_view_needs_attention_in_review_state(tmp_path: Path, xdg_state: Path) -> None:
    tracker = _tracker_with([])
    async with _RefineApp(tmp_path, tracker).run_test(headless=True) as pilot:
        await pilot.pause()
        view = pilot.app.query_one(RefineView)
        view._substate = "review"
        assert view.needs_attention() == "review ready"


async def test_refine_view_posts_attention_on_review_substate(
    tmp_path: Path, xdg_state: Path
) -> None:
    from cog.ui.messages import ViewAttention

    tracker = _tracker_with([])
    async with _RefineApp(tmp_path, tracker).run_test(headless=True) as pilot:
        await pilot.pause()
        view = pilot.app.query_one(RefineView)
        captured: list[ViewAttention] = []
        original_post = view.post_message

        def _capture(msg):
            if isinstance(msg, ViewAttention):
                captured.append(msg)
            return original_post(msg)

        view.post_message = _capture  # type: ignore[method-assign]

        view._switch_to("review")
        await pilot.pause()

        assert any(m.view_id == "refine" for m in captured)


async def test_refine_view_title_strip_marks_unchanged_when_titles_match(
    tmp_path: Path, xdg_state: Path
) -> None:
    tracker = _tracker_with([])
    async with _RefineApp(tmp_path, tracker).run_test(headless=True) as pilot:
        await pilot.pause()
        view = pilot.app.query_one(RefineView)

        async def _resolve_later() -> None:
            await asyncio.sleep(0.05)
            view.action_review_accept()

        pilot.app.run_worker(_resolve_later())
        task = asyncio.create_task(
            view.review(
                original_title="same",
                original_body="a",
                proposed_title="same",
                proposed_body="b",
                tmp_dir=tmp_path,
            )
        )
        await pilot.pause()
        strip = view.query_one("#review-title-strip", Static)
        assert "unchanged" in str(strip.renderable)
        await task


async def test_refine_view_renders_assignee_suffix(tmp_path: Path, xdg_state: Path) -> None:
    item = Item(
        tracker_id="gh",
        item_id="3",
        title="item 3",
        body="body",
        labels=(),
        comments=(),
        state="open",
        created_at=datetime(2026, 4, 20, tzinfo=UTC),
        updated_at=datetime(2026, 4, 20, tzinfo=UTC),
        url="",
        assignees=("bob",),
    )
    tracker = _tracker_with([item])
    async with _RefineApp(tmp_path, tracker).run_test(headless=True) as pilot:
        await pilot.pause()
        queue = pilot.app.query_one("#refine-queue", ListView)
        label_text = str(queue.children[0].query_one("Label").renderable)
        assert "(@bob)" in label_text
