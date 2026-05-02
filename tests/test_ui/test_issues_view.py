"""Tests for IssuesView (#189, #200)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from textual.app import App, ComposeResult
from textual.widgets import Input

from cog.core.errors import TrackerError
from cog.core.item import Comment, Item
from cog.ui.views.issues import IssuesView
from cog.ui.widgets.issues_browser import IssueList
from tests.fakes import FakeIssueTracker, make_item

_BASE_DT = datetime(2026, 1, 1, tzinfo=UTC)


def _item(
    item_id: str,
    title: str = "title",
    labels: tuple[str, ...] = (),
    state: str = "open",
    updated_at: datetime | None = None,
    assignees: tuple[str, ...] = (),
) -> Item:
    return make_item(
        item_id=item_id,
        title=title,
        labels=labels,
        state=state,
        updated_at=updated_at or _BASE_DT,
        assignees=assignees,
    )


class _IssuesApp(App):
    def __init__(self, tracker: FakeIssueTracker, project_dir: Path) -> None:
        super().__init__()
        self._tracker = tracker
        self._project_dir = project_dir
        # App-level fields expected by IssuesView
        self.issues_filter_query: str = "state:open"
        self.current_user_login: str | None = None

    def compose(self) -> ComposeResult:
        yield IssuesView(self._project_dir, self._tracker)

    def on_mount(self) -> None:
        view = self.query_one(IssuesView)
        self.call_after_refresh(view.on_show)


# ---------------------------------------------------------------------------
# First load
# ---------------------------------------------------------------------------


async def test_first_load_calls_list(tmp_path: Path) -> None:
    tracker = FakeIssueTracker([_item("1"), _item("2")])
    async with _IssuesApp(tracker, tmp_path).run_test(headless=True) as pilot:
        await pilot.pause(0.2)
        assert len(tracker.list_calls) >= 1


async def test_first_load_populates_cache(tmp_path: Path) -> None:
    items = [_item("1", title="First"), _item("2", title="Second")]
    tracker = FakeIssueTracker(items)
    async with _IssuesApp(tracker, tmp_path).run_test(headless=True) as pilot:
        await pilot.pause(0.2)
        view = pilot.app.query_one(IssuesView)
        assert len(view._cache) == 2


async def test_second_show_does_not_refetch(tmp_path: Path) -> None:
    tracker = FakeIssueTracker([_item("1")])
    async with _IssuesApp(tracker, tmp_path).run_test(headless=True) as pilot:
        await pilot.pause(0.2)
        initial_calls = len(tracker.list_calls)
        view = pilot.app.query_one(IssuesView)
        await view.on_show()
        await pilot.pause(0.1)
        assert len(tracker.list_calls) == initial_calls


async def test_first_load_fetches_open_only(tmp_path: Path) -> None:
    tracker = FakeIssueTracker([_item("1")])
    async with _IssuesApp(tracker, tmp_path).run_test(headless=True) as pilot:
        await pilot.pause(0.2)

        first_call = tracker.list_calls[0]
        assert first_call is not None
        assert first_call.state == "open"


# ---------------------------------------------------------------------------
# Refresh
# ---------------------------------------------------------------------------


async def test_refresh_action_refetches(tmp_path: Path) -> None:
    tracker = FakeIssueTracker([_item("1")])
    async with _IssuesApp(tracker, tmp_path).run_test(headless=True) as pilot:
        await pilot.pause(0.2)
        view = pilot.app.query_one(IssuesView)
        await view.action_refresh()
        await pilot.pause(0.1)
        assert len(tracker.list_calls) >= 2


async def test_refresh_preserves_focused_row_when_item_present(tmp_path: Path) -> None:
    items = [
        _item("1", updated_at=_BASE_DT + timedelta(hours=1)),
        _item("2", updated_at=_BASE_DT),
    ]
    tracker = FakeIssueTracker(items)
    async with _IssuesApp(tracker, tmp_path).run_test(headless=True) as pilot:
        await pilot.pause(0.3)
        view = pilot.app.query_one(IssuesView)
        issue_list = view.query_one(IssueList)
        focused = issue_list.focused_item()
        assert focused is not None
        focused_id = focused.item_id

        await view.action_refresh()
        await pilot.pause(0.2)
        new_focused = issue_list.focused_item()
        assert new_focused is not None
        assert new_focused.item_id == focused_id


async def test_refresh_error_keeps_last_known_rows(tmp_path: Path) -> None:
    tracker = FakeIssueTracker([_item("1")])
    async with _IssuesApp(tracker, tmp_path).run_test(headless=True) as pilot:
        await pilot.pause(0.2)
        tracker._list_error = TrackerError("network down")
        view = pilot.app.query_one(IssuesView)
        await view.action_refresh()
        await pilot.pause(0.1)
        assert len(view._cache) == 1


async def test_refresh_retries_closed_after_lazy_fetch_failure(tmp_path: Path) -> None:
    """If the closed lazy fetch failed, R should retry it (the status row tells the user to)."""
    tracker = FakeIssueTracker([_item("1", state="open")])
    async with _IssuesApp(tracker, tmp_path).run_test(headless=True) as pilot:
        await pilot.pause(0.2)
        view = pilot.app.query_one(IssuesView)
        # Simulate a failed lazy-fetch of closed
        view._closed_fetch_failed = True
        view._closed_loaded = False
        calls_before = len(tracker.list_calls)
        await view.action_refresh()
        await pilot.pause(0.2)
        # Two list calls: open + retry of closed
        assert len(tracker.list_calls) - calls_before == 2
        assert view._closed_loaded is True
        assert view._closed_fetch_failed is False


# ---------------------------------------------------------------------------
# Error states
# ---------------------------------------------------------------------------


async def test_first_load_error_shows_overlay(tmp_path: Path) -> None:
    tracker = FakeIssueTracker(list_error=TrackerError("boom"))
    async with _IssuesApp(tracker, tmp_path).run_test(headless=True) as pilot:
        await pilot.pause(0.2)
        overlay = pilot.app.query_one(IssueList).query_one("#list-overlay")
        assert overlay.has_class("visible")


# ---------------------------------------------------------------------------
# Focus and navigation
# ---------------------------------------------------------------------------


async def test_slash_focuses_filter_input(tmp_path: Path) -> None:
    tracker = FakeIssueTracker([])
    async with _IssuesApp(tracker, tmp_path).run_test(headless=True) as pilot:
        await pilot.pause(0.2)
        view = pilot.app.query_one(IssuesView)
        view.action_focus_search()
        await pilot.pause(0.1)
        inp = pilot.app.query_one("#issues-filter-input", Input)
        assert inp.has_focus


# ---------------------------------------------------------------------------
# Lazy-fetch closed
# ---------------------------------------------------------------------------


async def test_lazy_fetch_closed_triggered_on_state_widening(tmp_path: Path) -> None:
    open_item = _item("1", state="open")
    closed_item = _item("2", state="closed")
    tracker = FakeIssueTracker([open_item, closed_item])
    async with _IssuesApp(tracker, tmp_path).run_test(headless=True) as pilot:
        await pilot.pause(0.2)
        initial_calls = len(tracker.list_calls)
        # Widen to closed by changing input
        inp = pilot.app.query_one("#issues-filter-input", Input)
        inp.value = "state:all"
        await pilot.pause(0.3)
        # Should have fetched closed
        assert len(tracker.list_calls) > initial_calls


async def test_lazy_fetch_closed_not_repeated(tmp_path: Path) -> None:
    tracker = FakeIssueTracker([_item("1")])
    async with _IssuesApp(tracker, tmp_path).run_test(headless=True) as pilot:
        await pilot.pause(0.2)
        view = pilot.app.query_one(IssuesView)
        # Manually mark closed as loaded
        view._closed_loaded = True
        calls_before = len(tracker.list_calls)
        inp = pilot.app.query_one("#issues-filter-input", Input)
        inp.value = "state:all"
        await pilot.pause(0.2)
        # No new closed fetch because already loaded
        assert len(tracker.list_calls) == calls_before


# ---------------------------------------------------------------------------
# Status row
# ---------------------------------------------------------------------------


async def test_status_row_shows_count(tmp_path: Path) -> None:
    items = [_item("1"), _item("2")]
    tracker = FakeIssueTracker(items)
    async with _IssuesApp(tracker, tmp_path).run_test(headless=True) as pilot:
        await pilot.pause(0.2)
        statusbar = pilot.app.query_one("#issues-statusbar")
        text = str(statusbar.renderable)
        assert "of" in text
        assert "issues" in text


# ---------------------------------------------------------------------------
# In-session filter persistence
# ---------------------------------------------------------------------------


async def test_filter_persists_to_app(tmp_path: Path) -> None:
    tracker = FakeIssueTracker([_item("1")])
    async with _IssuesApp(tracker, tmp_path).run_test(headless=True) as pilot:
        await pilot.pause(0.2)
        inp = pilot.app.query_one("#issues-filter-input", Input)
        inp.value = "label:bug"
        await pilot.pause(0.1)
        assert pilot.app.issues_filter_query == "label:bug"


# ---------------------------------------------------------------------------
# Comments
# ---------------------------------------------------------------------------


async def test_side_pane_comments_fetched_after_debounce(tmp_path: Path) -> None:
    item_with_comments = make_item(
        item_id="1",
        comments=(Comment(author="alice", body="great issue", created_at=_BASE_DT),),
    )
    tracker = FakeIssueTracker([item_with_comments])

    async with _IssuesApp(tracker, tmp_path).run_test(headless=True) as pilot:
        await pilot.pause(0.2)
        view = pilot.app.query_one(IssuesView)
        view._load_comments(item_with_comments)
        await pilot.pause(0.3)
        assert "1" in tracker.get_calls


async def test_side_pane_comments_cache_hit_no_refetch(tmp_path: Path) -> None:
    item = _item("1")
    tracker = FakeIssueTracker([item])
    async with _IssuesApp(tracker, tmp_path).run_test(headless=True) as pilot:
        await pilot.pause(0.2)
        view = pilot.app.query_one(IssuesView)
        view._load_comments(item)
        await pilot.pause(0.3)
        initial_get_calls = len(tracker.get_calls)
        view._load_comments(item)
        await pilot.pause(0.1)
        assert len(tracker.get_calls) == initial_get_calls


async def test_side_pane_get_error_does_not_affect_statusbar(tmp_path: Path) -> None:
    item = _item("1")
    tracker = FakeIssueTracker([item], get_error=TrackerError("forbidden"))
    async with _IssuesApp(tracker, tmp_path).run_test(headless=True) as pilot:
        await pilot.pause(0.2)
        view = pilot.app.query_one(IssuesView)
        view._load_comments(item)
        await pilot.pause(0.3)
        statusbar = view.query_one("#issues-statusbar")
        assert "forbidden" not in str(statusbar.renderable or "")


# ---------------------------------------------------------------------------
# Close
# ---------------------------------------------------------------------------


async def test_close_confirmed_calls_tracker(tmp_path: Path) -> None:
    item = _item("42", title="Bug to close")
    tracker = FakeIssueTracker([item])
    async with _IssuesApp(tracker, tmp_path).run_test(headless=True) as pilot:
        await pilot.pause(0.2)
        view = pilot.app.query_one(IssuesView)
        # Simulate a confirmed close.
        view._on_close_confirmed(True, item)
        await pilot.pause(0.2)
        assert tracker.close_calls == ["42"]
        # Cache reflects new state.
        cached = next(it for it in view._cache if it.item_id == "42")
        assert cached.state == "closed"


async def test_close_cancelled_does_not_call_tracker(tmp_path: Path) -> None:
    item = _item("42")
    tracker = FakeIssueTracker([item])
    async with _IssuesApp(tracker, tmp_path).run_test(headless=True) as pilot:
        await pilot.pause(0.2)
        view = pilot.app.query_one(IssuesView)
        view._on_close_confirmed(False, item)
        await pilot.pause(0.1)
        assert tracker.close_calls == []


async def test_close_action_skips_already_closed_items(tmp_path: Path) -> None:
    item = _item("42", state="closed")
    tracker = FakeIssueTracker([item])
    async with _IssuesApp(tracker, tmp_path).run_test(headless=True) as pilot:
        await pilot.pause(0.2)
        view = pilot.app.query_one(IssuesView)
        # No focused item should be closeable when the row is already closed.
        view.action_close_issue()
        await pilot.pause(0.1)
        assert tracker.close_calls == []
