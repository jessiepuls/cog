"""Tests for RefineView (#124)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from textual.app import App, ComposeResult
from textual.widgets import ListView, Static

from cog.core.item import Comment, Item
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

        # Start review as a task; check the state while it is awaiting the future.
        task = asyncio.create_task(
            view.review(
                original_title="a",
                original_body="a-body",
                proposed_title="b",
                proposed_body="b-body",
                tmp_dir=tmp_path,
            )
        )
        # Give the event loop enough ticks for review() to reach its await point
        for _ in range(10):
            await asyncio.sleep(0)
        assert view._substate == "review"
        # Resolve the future directly so the finally block runs
        view.action_review_accept()
        await task


async def test_refine_view_review_panes_populated_with_bodies(
    tmp_path: Path, xdg_state: Path
) -> None:
    tracker = _tracker_with([])
    async with _RefineApp(tmp_path, tracker).run_test(headless=True) as pilot:
        await pilot.pause()
        view = pilot.app.query_one(RefineView)

        task = asyncio.create_task(
            view.review(
                original_title="orig-title",
                original_body="ORIGINAL body content",
                proposed_title="new-title",
                proposed_body="PROPOSED body content",
                tmp_dir=tmp_path,
            )
        )
        # Give event loop time to run review() up to its await point
        for _ in range(10):
            await asyncio.sleep(0)
        # Proposed body is mounted in the right pane during review
        prop = view.query_one("#review-proposed-body", Static)
        assert "PROPOSED" in prop.renderable.markup  # type: ignore[attr-defined]
        view.action_review_accept()
        await task


async def test_refine_view_review_proposed_body_in_scrollable_container(
    tmp_path: Path, xdg_state: Path
) -> None:
    from textual.containers import ScrollableContainer

    tracker = _tracker_with([])
    async with _RefineApp(tmp_path, tracker).run_test(headless=True) as pilot:
        await pilot.pause()
        view = pilot.app.query_one(RefineView)

        long_body = "\n".join(f"Line {i}" for i in range(120))
        task = asyncio.create_task(
            view.review(
                original_title="t",
                original_body="o",
                proposed_title="t",
                proposed_body=long_body,
                tmp_dir=tmp_path,
            )
        )
        for _ in range(10):
            await asyncio.sleep(0)

        # The proposed body Static must be inside a ScrollableContainer
        scroll = view.query_one("#review-proposed-scroll", ScrollableContainer)
        prop = scroll.query_one("#review-proposed-body", Static)
        assert prop is not None

        # The scroll wrapper is removed after review completes
        view.action_review_accept()
        await task
        assert len(view.query("#review-proposed-scroll")) == 0


async def test_refine_view_review_edit_updates_proposed_body_in_scroll(
    tmp_path: Path, xdg_state: Path
) -> None:
    from unittest.mock import patch

    tracker = _tracker_with([])
    async with _RefineApp(tmp_path, tracker).run_test(headless=True) as pilot:
        await pilot.pause()
        view = pilot.app.query_one(RefineView)

        task = asyncio.create_task(
            view.review(
                original_title="t",
                original_body="o",
                proposed_title="t",
                proposed_body="original proposed",
                tmp_dir=tmp_path,
            )
        )
        for _ in range(10):
            await asyncio.sleep(0)

        # Edit action must still find and update #review-proposed-body
        with patch("cog.ui.views.refine.suspend_and_edit", return_value="edited proposed"):
            await view.action_review_edit()

        prop = view.query_one("#review-proposed-body", Static)
        assert "edited proposed" in prop.renderable.markup  # type: ignore[attr-defined]

        view.action_review_accept()
        await task


async def test_refine_view_chat_pane_instance_preserved_across_review_swap(
    tmp_path: Path, xdg_state: Path
) -> None:
    """Regression: detaching/remounting ChatPaneWidget rebuilds its children
    (Textual reruns compose() on remount), which clears RichLog scrollback.
    Hide via display=False instead so the chat instance and its children
    survive the swap."""
    from textual.containers import Container
    from textual.widgets import RichLog

    from cog.ui.widgets.chat_pane import ChatPaneWidget

    tracker = _tracker_with([])
    async with _RefineApp(tmp_path, tracker).run_test(headless=True) as pilot:
        await pilot.pause()
        view = pilot.app.query_one(RefineView)

        right = view.query_one("#refine-right", Container)
        chat = ChatPaneWidget()
        view._chat_pane = chat
        await right.mount(chat)
        await pilot.pause()
        view._substate = "running"
        log_id = id(chat.query_one("#scrollback", RichLog))

        async def _accept_later() -> None:
            await asyncio.sleep(0.05)
            view.action_review_accept()

        pilot.app.run_worker(_accept_later())
        await view.review(
            original_title="orig",
            original_body="o",
            proposed_title="new",
            proposed_body="p",
            tmp_dir=tmp_path,
        )
        await pilot.pause()

        # Same chat instance still mounted, same RichLog inside
        children = list(right.children)
        assert chat in children
        assert chat.display is True
        assert id(chat.query_one("#scrollback", RichLog)) == log_id


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


# ---- _format_status tests ------------------------------------------------


@pytest.mark.parametrize(
    "verb,labels,expected",
    [
        ("Refining", ("p1", "bug"), "Refining #42 - My Title [p1, bug]"),
        ("Reviewing", (), "Reviewing #42 - My Title"),
        ("Completed", ("agent-ready",), "Completed #42 - My Title [agent-ready]"),
        ("Refining", (), "Refining #42 - My Title"),
    ],
)
async def test_format_status_produces_correct_string(
    tmp_path: Path, xdg_state: Path, verb: str, labels: tuple[str, ...], expected: str
) -> None:
    tracker = _tracker_with([])
    async with _RefineApp(tmp_path, tracker).run_test(headless=True) as pilot:
        await pilot.pause()
        view = pilot.app.query_one(RefineView)
        item = Item(
            tracker_id="gh",
            item_id="42",
            title="My Title",
            body="",
            labels=labels,
            comments=(),
            state="open",
            created_at=datetime(2026, 4, 20, tzinfo=UTC),
            updated_at=datetime(2026, 4, 20, tzinfo=UTC),
            url="",
        )
        assert view._format_status(verb, item=item) == expected


async def test_format_status_with_suffix(tmp_path: Path, xdg_state: Path) -> None:
    tracker = _tracker_with([])
    async with _RefineApp(tmp_path, tracker).run_test(headless=True) as pilot:
        await pilot.pause()
        view = pilot.app.query_one(RefineView)
        item = Item(
            tracker_id="gh",
            item_id="7",
            title="Something",
            body="",
            labels=(),
            comments=(),
            state="open",
            created_at=datetime(2026, 4, 20, tzinfo=UTC),
            updated_at=datetime(2026, 4, 20, tzinfo=UTC),
            url="",
        )
        result = view._format_status("Failed", item=item, suffix=": timeout")
        assert result == "Failed #7 - Something: timeout"


async def test_format_status_long_title_not_truncated(tmp_path: Path, xdg_state: Path) -> None:
    tracker = _tracker_with([])
    async with _RefineApp(tmp_path, tracker).run_test(headless=True) as pilot:
        await pilot.pause()
        view = pilot.app.query_one(RefineView)
        long_title = "x" * 300
        item = Item(
            tracker_id="gh",
            item_id="1",
            title=long_title,
            body="",
            labels=(),
            comments=(),
            state="open",
            created_at=datetime(2026, 4, 20, tzinfo=UTC),
            updated_at=datetime(2026, 4, 20, tzinfo=UTC),
            url="",
        )
        result = view._format_status("Refining", item=item)
        assert long_title in result


# ---- _render_left_pane tests ---------------------------------------------


def _make_item_with_comments(body: str, comments: list[tuple[str, str, datetime]]) -> Item:
    return Item(
        tracker_id="gh",
        item_id="1",
        title="T",
        body=body,
        labels=(),
        comments=tuple(Comment(author=a, body=b, created_at=ts) for a, b, ts in comments),
        state="open",
        created_at=datetime(2026, 4, 20, tzinfo=UTC),
        updated_at=datetime(2026, 4, 20, tzinfo=UTC),
        url="",
    )


async def test_render_left_pane_body_only(tmp_path: Path, xdg_state: Path) -> None:
    tracker = _tracker_with([])
    async with _RefineApp(tmp_path, tracker).run_test(headless=True) as pilot:
        await pilot.pause()
        view = pilot.app.query_one(RefineView)
        item = _make_item_with_comments("Just the body text.", [])
        view._render_left_pane(item)
        await pilot.pause()
        static = view.query_one("#refine-original-body", Static)
        assert "Just the body text." in static.renderable.markup  # type: ignore[attr-defined]


async def test_render_left_pane_empty_body(tmp_path: Path, xdg_state: Path) -> None:
    tracker = _tracker_with([])
    async with _RefineApp(tmp_path, tracker).run_test(headless=True) as pilot:
        await pilot.pause()
        view = pilot.app.query_one(RefineView)
        item = _make_item_with_comments("", [])
        view._render_left_pane(item)
        await pilot.pause()
        static = view.query_one("#refine-original-body", Static)
        assert "*(empty body)*" in static.renderable.markup  # type: ignore[attr-defined]


async def test_render_left_pane_with_one_comment(tmp_path: Path, xdg_state: Path) -> None:
    tracker = _tracker_with([])
    async with _RefineApp(tmp_path, tracker).run_test(headless=True) as pilot:
        await pilot.pause()
        view = pilot.app.query_one(RefineView)
        ts = datetime(2026, 3, 15, 10, 30, tzinfo=UTC)
        item = _make_item_with_comments("Body text.", [("alice", "A comment.", ts)])
        view._render_left_pane(item)
        await pilot.pause()
        markup = view.query_one("#refine-original-body", Static).renderable.markup  # type: ignore[attr-defined]
        assert "Body text." in markup
        assert "@alice" in markup
        assert "2026-03-15 10:30" in markup
        assert "A comment." in markup
        assert "---" in markup


async def test_render_left_pane_multiple_comments_no_trailing_separator(
    tmp_path: Path, xdg_state: Path
) -> None:
    tracker = _tracker_with([])
    async with _RefineApp(tmp_path, tracker).run_test(headless=True) as pilot:
        await pilot.pause()
        view = pilot.app.query_one(RefineView)
        ts = datetime(2026, 1, 1, tzinfo=UTC)
        item = _make_item_with_comments(
            "Body.",
            [("alice", "First.", ts), ("bob", "Second.", ts), ("carol", "Third.", ts)],
        )
        view._render_left_pane(item)
        await pilot.pause()
        markup = view.query_one("#refine-original-body", Static).renderable.markup  # type: ignore[attr-defined]
        assert "@alice" in markup
        assert "@bob" in markup
        assert "@carol" in markup
        # Separators appear between comments, not after the last
        assert markup.count("---") == 3


# ---- _apply_split / splitter tests ---------------------------------------


async def test_apply_split_clamps_at_20_minimum(tmp_path: Path, xdg_state: Path) -> None:
    tracker = _tracker_with([])
    async with _RefineApp(tmp_path, tracker).run_test(headless=True) as pilot:
        await pilot.pause()
        view = pilot.app.query_one(RefineView)
        view._split_pct = 20
        view.action_narrow_issue()
        assert view._split_pct == 20


async def test_apply_split_clamps_at_80_maximum(tmp_path: Path, xdg_state: Path) -> None:
    tracker = _tracker_with([])
    async with _RefineApp(tmp_path, tracker).run_test(headless=True) as pilot:
        await pilot.pause()
        view = pilot.app.query_one(RefineView)
        view._split_pct = 80
        view.action_widen_issue()
        assert view._split_pct == 80


async def test_apply_split_step_is_5(tmp_path: Path, xdg_state: Path) -> None:
    tracker = _tracker_with([])
    async with _RefineApp(tmp_path, tracker).run_test(headless=True) as pilot:
        await pilot.pause()
        view = pilot.app.query_one(RefineView)
        view._split_pct = 50
        view.action_narrow_issue()
        assert view._split_pct == 45
        view.action_widen_issue()
        view.action_widen_issue()
        assert view._split_pct == 55


async def test_splitter_bindings_visible_in_running_and_review(
    tmp_path: Path, xdg_state: Path
) -> None:
    tracker = _tracker_with([])
    async with _RefineApp(tmp_path, tracker).run_test(headless=True) as pilot:
        await pilot.pause()
        view = pilot.app.query_one(RefineView)
        # Idle: hidden
        assert view.check_action("narrow_issue", ()) is None
        assert view.check_action("widen_issue", ()) is None
        # Running: visible
        view._substate = "running"
        assert view.check_action("narrow_issue", ()) is True
        assert view.check_action("widen_issue", ()) is True
        # Review: visible
        view._substate = "review"
        assert view.check_action("narrow_issue", ()) is True
        assert view.check_action("widen_issue", ()) is True
