"""IssuesView — read-only issue browser (#189)."""

from __future__ import annotations

from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.timer import Timer
from textual.widget import Widget
from textual.widgets import Static

from cog.core.item import Item
from cog.core.tracker import IssueTracker, ItemListFilter
from cog.ui.widgets.issues_browser import (
    IssueFilterRow,
    IssueList,
    IssuePreview,
    _apply_filter,
)

_FETCH_FILTER = ItemListFilter(state="all", limit=1000)


class IssuesView(Widget, can_focus=True):
    """Read-only issues browser: filter bar + list pane + side pane."""

    BINDINGS = [
        Binding("r", "refresh", "Refresh"),
        Binding("ctrl+l", "clear_filters", "Clear filters"),
        Binding("/", "focus_search", "Search"),
    ]

    DEFAULT_CSS = """
    IssuesView {
        layout: vertical;
        height: 1fr;
    }
    IssuesView #issues-statusbar {
        height: 1;
        padding: 0 1;
        color: $text-muted;
    }
    IssuesView #issues-body {
        height: 1fr;
        layout: horizontal;
    }
    IssuesView IssueList {
        width: 1fr;
        height: 1fr;
    }
    IssuesView IssuePreview {
        width: 1fr;
        height: 1fr;
    }
    """

    def __init__(self, project_dir: Path, tracker: IssueTracker) -> None:
        super().__init__(id="view-issues")
        self._project_dir = project_dir
        self._tracker = tracker
        self._cache: list[Item] = []
        self._cache_loaded = False
        self._active_filter = ItemListFilter(state="open")
        # (item_id, updated_at) -> Item with comments
        self._comments_cache: dict[tuple[str, str], Item] = {}
        self._focus_debounce: Timer | None = None
        self._label_options: list[tuple[str, str]] = []
        self._assignee_options: list[tuple[str, str]] = []

    def compose(self) -> ComposeResult:
        yield IssueFilterRow()
        with Horizontal(id="issues-body"):
            yield IssueList()
            yield IssuePreview()
        yield Static("", id="issues-statusbar")

    async def on_mount(self) -> None:
        pass

    async def on_show(self) -> None:
        if not self._cache_loaded:
            await self._first_load()

    def focus_content(self) -> None:
        try:
            self.query_one(IssueList).focus_list()
        except Exception:  # noqa: BLE001
            pass

    async def _first_load(self) -> None:
        issue_list = self.query_one(IssueList)
        issue_list.show_overlay("[dim]Loading issues…[/dim]")
        self._set_status("Loading…")
        try:
            items = await self._tracker.list(_FETCH_FILTER)
        except Exception as e:  # noqa: BLE001
            issue_list.show_overlay(
                f"[red]Error loading issues: {e}[/red]\n[dim]Press R to retry[/dim]"
            )
            self._set_status("")
            return
        self._cache = items
        self._cache_loaded = True
        self._rebuild_dropdown_options()
        self._push_filter()
        self._set_status(f"Loaded {len(items)} issues")

    async def action_refresh(self) -> None:
        self._set_status("[dim]Refreshing…[/dim]")
        focused_id = self.query_one(IssueList).focused_item_id()
        try:
            items = await self._tracker.list(_FETCH_FILTER)
        except Exception as e:  # noqa: BLE001
            self._set_status(f"[red]Refresh failed: {e}[/red]")
            return
        self._cache = items
        self._cache_loaded = True
        self._rebuild_dropdown_options()
        self._push_filter(preserve_id=focused_id)
        self._set_status(f"Loaded just now ({len(items)} issues)")

    def action_clear_filters(self) -> None:
        self._active_filter = ItemListFilter(state="open")
        self.query_one(IssueFilterRow).clear_all()

    def action_focus_search(self) -> None:
        try:
            inp = self.query_one("#filter-search")
            if inp.has_focus:
                return
            self.query_one(IssueFilterRow).focus_search()
        except Exception:  # noqa: BLE001
            pass

    def on_issue_filter_row_filter_changed(self, event: IssueFilterRow.FilterChanged) -> None:
        event.stop()
        self._active_filter = event.filter
        self._push_filter()

    def _push_filter(self, *, preserve_id: str | None = None) -> None:
        filtered = _apply_filter(self._cache, self._active_filter)
        issue_list = self.query_one(IssueList)
        preview = self.query_one(IssuePreview)
        self.run_worker(
            issue_list.set_items(filtered, preserve_id=preserve_id),
            exclusive=True,
            group="set_items",
        )
        if not filtered:
            preview.show_empty()

    def _rebuild_dropdown_options(self) -> None:
        # Label options: dedupe by name, preserve color
        seen_labels: dict[str, str] = {}
        for item in self._cache:
            for label_name in item.labels:
                if label_name not in seen_labels:
                    seen_labels[label_name] = "cccccc"
        self._label_options = sorted(seen_labels.items())

        # Assignee options: synthetic (unassigned) + deduped logins
        seen_assignees: set[str] = set()
        for item in self._cache:
            seen_assignees.update(item.assignees)
        self._assignee_options = [("(unassigned)", "888888")] + sorted(
            (a, "cccccc") for a in seen_assignees
        )

        filter_row = self.query_one(IssueFilterRow)
        filter_row.update_label_options(self._label_options)
        filter_row.update_assignee_options(self._assignee_options)

    def on_issue_list_item_focused(self, event: IssueList.ItemFocused) -> None:
        event.stop()
        if event.item is None:
            self.query_one(IssuePreview).show_empty()
            return
        self.query_one(IssuePreview).show_item(event.item)
        if self._focus_debounce is not None:
            try:
                self._focus_debounce.stop()
            except Exception:  # noqa: BLE001
                pass
        self._focus_debounce = self.set_timer(
            0.15, lambda item=event.item: self._load_comments(item)
        )

    def _load_comments(self, item: Item) -> None:
        cache_key = (item.item_id, item.updated_at.isoformat())
        if cache_key in self._comments_cache:
            self.query_one(IssuePreview).update_comments(self._comments_cache[cache_key])
            return
        self.run_worker(
            self._fetch_comments(item, cache_key),
            exclusive=False,
            group=f"comments-{item.item_id}",
        )

    async def _fetch_comments(self, item: Item, cache_key: tuple[str, str]) -> None:
        try:
            full_item = await self._tracker.get(item.item_id)
        except Exception as e:  # noqa: BLE001
            self.query_one(IssuePreview).show_comments_error(item.item_id, str(e))
            return
        self._comments_cache[cache_key] = full_item
        self.query_one(IssuePreview).update_comments(full_item)

    def _set_status(self, text: str) -> None:
        try:
            self.query_one("#issues-statusbar", Static).update(text)
        except Exception:  # noqa: BLE001
            pass
