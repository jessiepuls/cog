"""IssuesView — read-only issue browser with workflow launch (#189, #192, #200)."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Literal

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.events import Key
from textual.timer import Timer
from textual.widget import Widget
from textual.widgets import Input, Static

from cog.core.item import Item
from cog.core.tracker import IssueTracker, ItemListFilter
from cog.ui.messages import LaunchSlotRequest
from cog.ui.screens.close_confirm import CloseConfirmScreen
from cog.ui.widgets.filter_query import FilterSuggester, ParsedQuery, apply_parsed, parse_query
from cog.ui.widgets.issues_browser import IssueList, IssuePreview

_OPEN_FILTER = ItemListFilter(state="open", limit=1000)
_CLOSED_FILTER = ItemListFilter(state="closed", limit=1000)

_DEFAULT_QUERY = "state:open"

_WorkflowType = Literal["refine", "implement"]


def _recommended_workflow(item: Item) -> _WorkflowType | None:
    """Return the recommended workflow given the item's labels, or None."""
    if "needs-refinement" in item.labels:
        return "refine"
    if "agent-ready" in item.labels:
        return "implement"
    return None


def _launch_confirm_message(workflow: _WorkflowType, item: Item) -> str | None:
    """Return a confirmation prompt for a non-recommended launch, or None if recommended."""
    recommended = _recommended_workflow(item)
    if recommended == workflow:
        return None  # Recommended — no confirm needed
    item_ref = f"#{item.item_id}"
    if "needs-refinement" in item.labels and workflow == "implement":
        return f"{item_ref} is needs-refinement, not agent-ready. Implement anyway?"
    if "agent-ready" in item.labels and workflow == "refine":
        return f"{item_ref} is agent-ready, not needs-refinement. Refine anyway?"
    verb = workflow.capitalize()
    return f"{item_ref} has no workflow labels. {verb} anyway?"


class IssuesView(Widget, can_focus=True):
    """Read-only issues browser: typed-query filter + list pane + side pane.

    Also the launch point for dynamic workflow slots (#192): press `r` to
    refine or `i` to implement the selected item.
    """

    BINDINGS = [
        Binding("ctrl+r", "refresh", "Refresh"),
        Binding("r", "refine_item", "Refine"),
        Binding("i", "implement_item", "Implement"),
        Binding("/", "focus_search", "Search"),
        Binding("c", "close_issue", "Close"),
        Binding("ctrl+comma", "narrow_list", "Narrow list"),
        Binding("ctrl+full_stop", "widen_list", "Widen list"),
    ]

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        # When the filter input has focus, every keystroke is text input — don't
        # let ancestor single-letter bindings swallow them.
        if action in ("refresh", "focus_search", "close_issue", "refine_item", "implement_item"):
            try:
                if self.query_one("#issues-filter-input", Input).has_focus:
                    return False
            except Exception:  # noqa: BLE001
                pass
        return True

    DEFAULT_CSS = """
    IssuesView {
        layout: vertical;
        height: 1fr;
    }
    IssuesView #issues-filter-input {
        height: 3;
        border: tall $primary;
        background: $surface;
        padding: 0 1;
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
        width: 2fr;
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
        self._closed_loaded = False
        self._open_total: int | None = None
        self._closed_total: int | None = None
        self._closed_fetch_failed = False
        self._parsed: ParsedQuery = parse_query(_DEFAULT_QUERY)
        self._comments_cache: dict[tuple[str, str], Item] = {}
        self._focus_debounce: Timer | None = None
        self._status_reset_timer: Timer | None = None
        self._filter_debounce: Timer | None = None
        # List pane width as a percentage of the body width. Adjustable via
        # ctrl+comma / ctrl+full_stop, matching the refine view's convention.
        self._split_pct: int = 67

    def compose(self) -> ComposeResult:
        suggester = FilterSuggester(
            get_labels=self._known_labels,
            get_assignees=self._known_assignees,
            get_current_user_login=lambda: getattr(self.app, "current_user_login", None),
        )
        yield Input(
            value=_DEFAULT_QUERY,
            suggester=suggester,
            id="issues-filter-input",
        )
        yield Static("", id="issues-statusbar")
        with Horizontal(id="issues-body"):
            yield IssueList()
            yield IssuePreview()

    async def on_mount(self) -> None:
        seed = getattr(self.app, "issues_filter_query", _DEFAULT_QUERY)
        inp = self.query_one("#issues-filter-input", Input)
        inp.value = seed
        self._parsed = parse_query(seed)

    async def on_show(self) -> None:
        if not self._cache_loaded:
            await self._first_load()
        self.query_one("#issues-filter-input", Input).focus()

    def focus_content(self) -> None:
        try:
            self.query_one(IssueList).focus_list()
        except Exception:  # noqa: BLE001
            pass

    def _known_labels(self) -> list[str]:
        seen: set[str] = set()
        for item in self._cache:
            seen.update(item.labels)
        return sorted(seen)

    def _known_assignees(self) -> list[str]:
        seen: set[str] = set()
        for item in self._cache:
            seen.update(item.assignees)
        return sorted(seen)

    async def _first_load(self) -> None:
        issue_list = self.query_one(IssueList)
        issue_list.show_overlay("[dim]Loading issues…[/dim]")
        self._set_status("Loading…")
        try:
            result = await self._tracker.list(_OPEN_FILTER)
        except Exception as e:  # noqa: BLE001
            issue_list.show_overlay(
                f"[red]Error loading issues: {e}[/red]\n[dim]Press Ctrl+R to retry[/dim]"
            )
            self._set_status(f"Error loading issues: {e} — press Ctrl+R to retry")
            return
        self._cache = result.items
        self._open_total = result.total
        self._cache_loaded = True
        self._push_filter()
        self._update_status()

        # If the initial query already requests closed, kick off lazy fetch
        if self._parsed.state_set and "closed" in self._parsed.state_set:
            self.run_worker(self._lazy_fetch_closed(), exclusive=False, group="fetch-closed")

    async def _lazy_fetch_closed(self) -> None:
        if self._closed_loaded:
            return
        self._closed_fetch_failed = False
        self._set_status("Loading closed issues…")
        try:
            result = await self._tracker.list(_CLOSED_FILTER)
        except Exception:  # noqa: BLE001
            self._closed_fetch_failed = True
            self._set_status("Failed to load closed issues — press Ctrl+R to retry")
            return
        # Merge and re-sort by updated_at desc, deduplicating by item_id
        cached_ids = {i.item_id for i in self._cache}
        new_closed = [i for i in result.items if i.item_id not in cached_ids]
        self._cache = sorted(
            list(self._cache) + new_closed,
            key=lambda i: (i.updated_at, int(i.item_id) if i.item_id.isdigit() else 0),
            reverse=True,
        )
        self._closed_total = result.total
        self._closed_loaded = True
        self._push_filter()
        self._update_status()

    async def action_refresh(self) -> None:
        self._set_status("[dim]Refreshing…[/dim]")
        focused_id = self.query_one(IssueList).focused_item_id()
        try:
            open_result = await self._tracker.list(_OPEN_FILTER)
        except Exception as e:  # noqa: BLE001
            self._set_status(f"[red]Refresh failed: {e}[/red]")
            return

        if self._closed_loaded or self._closed_fetch_failed:
            try:
                closed_result = await self._tracker.list(_CLOSED_FILTER)
            except Exception as e:  # noqa: BLE001
                self._set_status(f"[red]Refresh failed: {e}[/red]")
                return
            self._closed_total = closed_result.total
            self._closed_loaded = True
            self._closed_fetch_failed = False
            closed_items = closed_result.items
        else:
            closed_items = []

        open_ids = {i.item_id for i in open_result.items}
        merged = sorted(
            open_result.items + [i for i in closed_items if i.item_id not in open_ids],
            key=lambda i: (i.updated_at, int(i.item_id) if i.item_id.isdigit() else 0),
            reverse=True,
        )
        self._cache = merged
        self._open_total = open_result.total
        self._cache_loaded = True
        self._push_filter(preserve_id=focused_id)

        n_matching = len(apply_parsed(self._cache, self._parsed, current_user_login=self._login()))
        total = (self._open_total or 0) + (self._closed_total or 0)
        self._set_status(f"Loaded just now ({n_matching} of {total} issues)")
        self._cancel_timer(self._status_reset_timer)
        self._status_reset_timer = self.set_timer(3.0, self._update_status)

    def action_focus_search(self) -> None:
        inp = self.query_one("#issues-filter-input", Input)
        inp.focus()

    def action_narrow_list(self) -> None:
        self._split_pct = max(20, self._split_pct - 5)
        self._apply_split()

    def action_widen_list(self) -> None:
        self._split_pct = min(80, self._split_pct + 5)
        self._apply_split()

    def action_close_issue(self) -> None:
        item = self.query_one(IssueList).focused_item()
        if item is None or item.state == "closed":
            return
        self.app.push_screen(
            CloseConfirmScreen(item.item_id, item.title),
            lambda confirmed, it=item: self._on_close_confirmed(confirmed, it),
        )

    def _on_close_confirmed(self, confirmed: bool | None, item: Item) -> None:
        if not confirmed:
            return
        self.run_worker(self._close_item(item), exclusive=False, group=f"close-{item.item_id}")

    async def _close_item(self, item: Item) -> None:
        self._set_status(f"Closing #{item.item_id}…")
        try:
            await self._tracker.close(item)
        except Exception as e:  # noqa: BLE001
            self._set_status(f"[red]Close failed: {e}[/red]")
            return
        # Reflect in cache so the list updates without a full refetch.
        self._cache = [
            replace(it, state="closed") if it.item_id == item.item_id else it for it in self._cache
        ]
        if self._open_total is not None and item.state == "open":
            self._open_total = max(0, self._open_total - 1)
        if self._closed_loaded and self._closed_total is not None:
            self._closed_total += 1
        self._push_filter()
        self._set_status(f"Closed #{item.item_id}")
        self._cancel_timer(self._status_reset_timer)
        self._status_reset_timer = self.set_timer(3.0, self._update_status)

    # -------------------------------------------------------------------------
    # Workflow launch actions (#192)
    # -------------------------------------------------------------------------

    def action_refine_item(self) -> None:
        self._request_launch("refine")

    def action_implement_item(self) -> None:
        self._request_launch("implement")

    def _request_launch(self, workflow: _WorkflowType) -> None:
        item = self.query_one(IssueList).focused_item()
        if item is None:
            return
        confirm_msg = _launch_confirm_message(workflow, item)
        if confirm_msg is None:
            self._do_launch(workflow, item)
            return
        from cog.ui.screens.launch_confirm import LaunchConfirmScreen

        self.app.push_screen(
            LaunchConfirmScreen(confirm_msg),
            lambda confirmed, wf=workflow, it=item: self._on_launch_confirmed(confirmed, wf, it),
        )

    def _on_launch_confirmed(
        self, confirmed: bool | None, workflow: _WorkflowType, item: Item
    ) -> None:
        if confirmed:
            self._do_launch(workflow, item)

    def _do_launch(self, workflow: _WorkflowType, item: Item) -> None:
        self.post_message(LaunchSlotRequest(workflow, item))

    # -------------------------------------------------------------------------

    def _apply_split(self) -> None:
        try:
            issue_list = self.query_one(IssueList)
            issue_list.styles.width = f"{self._split_pct}%"
        except Exception:  # noqa: BLE001
            pass

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "issues-filter-input":
            return
        event.stop()
        query = event.value
        # Persist immediately so a view-switch mid-typing doesn't lose state.
        if hasattr(self.app, "issues_filter_query"):
            self.app.issues_filter_query = query
        # Debounce the actual filter+rebuild — every keystroke would otherwise
        # cancel the in-flight ListView rebuild via exclusive workers.
        self._cancel_timer(self._filter_debounce)
        self._filter_debounce = self.set_timer(0.12, lambda q=query: self._apply_query(q))

    def _apply_query(self, query: str) -> None:
        prev_parsed = self._parsed
        self._parsed = parse_query(query)

        # Check if we need to lazy-fetch closed
        needs_closed = self._parsed.state_set is not None and "closed" in self._parsed.state_set
        prev_needed_closed = prev_parsed.state_set is not None and "closed" in prev_parsed.state_set
        if needs_closed and not prev_needed_closed and not self._closed_loaded:
            self.run_worker(self._lazy_fetch_closed(), exclusive=False, group="fetch-closed")

        self._push_filter()
        if not (needs_closed and not self._closed_loaded):
            self._update_status()

    def on_key(self, event: Key) -> None:
        """Enter/Down in the filter input moves focus to the list."""
        inp = self.query_one("#issues-filter-input", Input)
        if inp.has_focus and event.key in ("enter", "down"):
            event.stop()
            self.query_one(IssueList).focus_list()

    def on_issue_list_item_focused(self, event: IssueList.ItemFocused) -> None:
        event.stop()
        if event.item is None:
            self.query_one(IssuePreview).show_empty()
            return
        self.query_one(IssuePreview).show_item(event.item)
        self._cancel_timer(self._focus_debounce)
        self._focus_debounce = self.set_timer(
            0.15, lambda item=event.item: self._load_comments(item)
        )

    def _push_filter(self, *, preserve_id: str | None = None) -> None:
        filtered = apply_parsed(self._cache, self._parsed, current_user_login=self._login())
        issue_list = self.query_one(IssueList)
        preview = self.query_one(IssuePreview)

        # Pass a callable so the coroutine is only created when the worker runs,
        # avoiding "coroutine was never awaited" warnings from exclusive cancellation.
        async def _do_set() -> None:
            await issue_list.set_items(filtered, preserve_id=preserve_id)

        self.run_worker(_do_set, exclusive=True, group="set_items")  # type: ignore[arg-type]
        if not filtered:
            preview.show_empty()

    def _update_status(self) -> None:
        if self._closed_fetch_failed:
            self._set_status("Failed to load closed issues — press Ctrl+R to retry")
            return
        if self._open_total is None:
            return
        n_matching = len(apply_parsed(self._cache, self._parsed, current_user_login=self._login()))
        total = (self._open_total or 0) + (self._closed_total or 0)
        self._set_status(f"{n_matching} of {total} issues")

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

    def _login(self) -> str | None:
        return getattr(self.app, "current_user_login", None)

    def _cancel_timer(self, timer: Timer | None) -> None:
        if timer is not None:
            try:
                timer.stop()
            except Exception:  # noqa: BLE001
                pass
