"""Tests for IssuesView (#189)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from textual.app import App, ComposeResult

from cog.core.errors import TrackerError
from cog.core.item import Comment, Item
from cog.ui.views.issues import IssuesView
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

    def compose(self) -> ComposeResult:
        yield IssuesView(self._project_dir, self._tracker)

    def on_mount(self) -> None:
        # Simulate the view becoming visible on first show
        view = self.query_one(IssuesView)
        self.call_after_refresh(view.on_show)


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
        from cog.ui.widgets.issues_browser import IssueList

        issue_list = view.query_one(IssueList)
        # Item "1" should be first (newer updated_at)
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
        # Now make subsequent list() calls fail
        tracker._list_error = TrackerError("network down")
        view = pilot.app.query_one(IssuesView)
        await view.action_refresh()
        await pilot.pause(0.1)
        # Cache should still have the original item
        assert len(view._cache) == 1


async def test_first_load_error_shows_message(tmp_path: Path) -> None:
    tracker = FakeIssueTracker(list_error=TrackerError("boom"))
    async with _IssuesApp(tracker, tmp_path).run_test(headless=True) as pilot:
        await pilot.pause(0.2)
        from cog.ui.widgets.issues_browser import IssueList

        overlay = pilot.app.query_one(IssueList).query_one("#list-overlay")
        assert overlay.has_class("visible")


async def test_slash_focuses_search_input(tmp_path: Path) -> None:
    tracker = FakeIssueTracker([])
    async with _IssuesApp(tracker, tmp_path).run_test(headless=True) as pilot:
        await pilot.pause(0.2)
        view = pilot.app.query_one(IssuesView)
        view.action_focus_search()
        await pilot.pause(0.1)
        from textual.widgets import Input

        search_input = pilot.app.query_one("#filter-search", Input)
        assert search_input.has_focus


async def test_ctrl_l_clears_filters(tmp_path: Path) -> None:
    tracker = FakeIssueTracker([_item("1"), _item("2", state="closed")])
    async with _IssuesApp(tracker, tmp_path).run_test(headless=True) as pilot:
        await pilot.pause(0.2)
        view = pilot.app.query_one(IssuesView)
        view.action_clear_filters()
        await pilot.pause(0.1)
        from cog.core.tracker import ItemListFilter

        assert view._active_filter == ItemListFilter(state="open")


async def test_closed_toggle_shows_closed_items(tmp_path: Path) -> None:
    items = [_item("1", state="open"), _item("2", state="closed")]
    tracker = FakeIssueTracker(items)
    async with _IssuesApp(tracker, tmp_path).run_test(headless=True) as pilot:
        await pilot.pause(0.2)
        view = pilot.app.query_one(IssuesView)
        # Default: closed hidden
        from cog.ui.widgets.issues_browser import IssueFilterRow

        filter_row = view.query_one(IssueFilterRow)
        assert not filter_row.show_closed

        # Toggle closed on
        filter_row.toggle_closed()
        await pilot.pause(0.1)
        assert view._active_filter.state == "all"


async def test_side_pane_comments_fetched_after_debounce(tmp_path: Path) -> None:
    item_with_comments = make_item(
        item_id="1",
        comments=(Comment(author="alice", body="great issue", created_at=_BASE_DT),),
    )
    tracker = FakeIssueTracker([_item("1")])
    # Override get to return item with comments
    tracker._items = [item_with_comments]

    async with _IssuesApp(tracker, tmp_path).run_test(headless=True) as pilot:
        await pilot.pause(0.2)
        view = pilot.app.query_one(IssuesView)
        # Simulate focus on item 1
        view._load_comments(item_with_comments)
        await pilot.pause(0.3)
        # get should have been called
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
        # Second load for same item+updated_at should use cache
        view._load_comments(item)
        await pilot.pause(0.1)
        assert len(tracker.get_calls) == initial_get_calls


async def test_side_pane_get_error_shows_per_item_error(tmp_path: Path) -> None:
    item = _item("1")
    tracker = FakeIssueTracker([item], get_error=TrackerError("forbidden"))
    async with _IssuesApp(tracker, tmp_path).run_test(headless=True) as pilot:
        await pilot.pause(0.2)
        view = pilot.app.query_one(IssuesView)
        view._load_comments(item)
        await pilot.pause(0.3)
        # Status bar in view should be unaffected (no error text there)
        statusbar = view.query_one("#issues-statusbar")
        assert "forbidden" not in (statusbar.renderable or "")
