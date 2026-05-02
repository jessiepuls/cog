"""Widgets for the read-only Issues browser view (#189)."""

from __future__ import annotations

from datetime import UTC, datetime

from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.events import Resize
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Label, ListItem, ListView, Markdown, Static

from cog.core.item import Item

# Cog-relevant labels: always pinned in row rendering, never dropped.
_COG_LABELS = frozenset({"needs-refinement", "agent-ready", "agent-failed", "partially-refined"})


def _row_text(item: Item, width: int = 80) -> str:
    """Render a row for IssueList: title on line 1, labels indented on line 2."""
    num = f"#{item.item_id:<4}"
    cog = [lbl for lbl in item.labels if lbl in _COG_LABELS]
    other = [lbl for lbl in item.labels if lbl not in _COG_LABELS]
    has_failed = "agent-failed" in item.labels
    glyph = " ⚠" if has_failed else ""

    label_parts = cog + other
    # Only `[` is special in Textual markup; `]` only matters when paired with
    # an opening `[`, so escaping it would render a literal backslash.
    chips_str = " ".join(rf"\[{lbl}]" for lbl in label_parts) + glyph

    # Title gets the full row (minus the number prefix and a couple chars padding)
    title_budget = max(10, width - 6 - 2)
    title = item.title if len(item.title) <= title_budget else item.title[: title_budget - 1] + "…"

    # Indent label line under the title (after the "#NNNN " number column)
    indent = " " * 6

    dim = item.state == "closed"
    if dim:
        if chips_str:
            return f"[dim]{num} [strike]{title}[/strike]\n{indent}{chips_str}[/dim]"
        return f"[dim]{num} [strike]{title}[/strike][/dim]"
    if chips_str:
        return f"{num} {title}\n{indent}{chips_str}"
    return f"{num} {title}"


class IssueList(Widget):
    """ListView showing issues as single-line rows."""

    class ItemFocused(Message):
        def __init__(self, sender: IssueList, item: Item | None) -> None:
            super().__init__()
            self.issue_list = sender
            self.item = item

    DEFAULT_CSS = """
    IssueList {
        height: 1fr;
    }
    IssueList ListView {
        height: 1fr;
    }
    IssueList #list-overlay {
        height: 1fr;
        align: center middle;
        display: none;
    }
    IssueList #list-overlay.visible {
        display: block;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self._items: list[Item] = []

    def compose(self) -> ComposeResult:
        yield ListView(id="issue-listview")
        yield Static("", id="list-overlay")

    def focused_item(self) -> Item | None:
        lv = self.query_one("#issue-listview", ListView)
        idx = lv.index
        if idx is not None and 0 <= idx < len(self._items):
            return self._items[idx]
        return None

    def focused_item_id(self) -> str | None:
        item = self.focused_item()
        return item.item_id if item else None

    async def set_items(self, items: list[Item], *, preserve_id: str | None = None) -> None:
        self._items = items
        await self._render_rows(preserve_id=preserve_id)

    async def _render_rows(self, *, preserve_id: str | None = None) -> None:
        lv = self.query_one("#issue-listview", ListView)
        overlay = self.query_one("#list-overlay", Static)
        await lv.clear()
        if not self._items:
            overlay.add_class("visible")
            lv.display = False
            return
        overlay.remove_class("visible")
        lv.display = True
        width = self.size.width or 80
        new_items = [
            ListItem(Label(_row_text(item, width)), id=f"issue-{item.item_id}")
            for item in self._items
        ]
        # Single mount() call instead of N awaited appends — one DOM update,
        # one re-layout, instead of yielding to the event loop per item.
        await lv.extend(new_items)
        if preserve_id:
            for i, it in enumerate(self._items):
                if it.item_id == preserve_id:
                    lv.index = i
                    return
        lv.index = 0

    async def on_resize(self, _: Resize) -> None:
        await self._render_rows(preserve_id=self.focused_item_id())

    def show_overlay(self, text: str) -> None:
        overlay = self.query_one("#list-overlay", Static)
        lv = self.query_one("#issue-listview", ListView)
        overlay.update(text)
        overlay.add_class("visible")
        lv.display = False

    def hide_overlay(self) -> None:
        overlay = self.query_one("#list-overlay", Static)
        overlay.remove_class("visible")
        self.query_one("#issue-listview", ListView).display = True

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        event.stop()
        self.post_message(self.ItemFocused(self, self.focused_item()))

    def focus_list(self) -> None:
        self.query_one("#issue-listview", ListView).focus()


class IssuePreview(Widget):
    """Side pane showing selected item body + comments."""

    DEFAULT_CSS = """
    IssuePreview {
        height: 1fr;
        border-left: solid $primary;
    }
    IssuePreview VerticalScroll {
        height: 1fr;
        padding: 0 1;
    }
    IssuePreview #preview-empty {
        height: 1fr;
        align: center middle;
        color: $text-muted;
    }
    IssuePreview #preview-scroll {
        display: none;
    }
    IssuePreview #preview-scroll.visible {
        display: block;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self._current_item: Item | None = None
        self._comments_status: str | None = None  # None=loaded, str=status text

    def compose(self) -> ComposeResult:
        yield Static(
            "Select an issue from the list to see details.",
            id="preview-empty",
        )
        with VerticalScroll(id="preview-scroll"):
            yield Static("", id="preview-header")
            yield Markdown("", id="preview-body")
            yield Static("─" * 40, id="preview-separator")
            yield Static("", id="preview-comments-status")
            yield Static("", id="preview-comments")

    def show_empty(self) -> None:
        self.query_one("#preview-empty").display = True
        self.query_one("#preview-scroll").display = False

    def show_item(self, item: Item) -> None:
        self._current_item = item
        self.query_one("#preview-empty").display = False
        scroll = self.query_one("#preview-scroll")
        scroll.display = True

        labels_str = ", ".join(item.labels) if item.labels else "(none)"
        assignees_str = (
            "  ·  " + "  ".join(f"@{a}" for a in item.assignees) if item.assignees else ""
        )
        state_color = "green" if item.state == "open" else "red"
        header = (
            f"[bold]#{item.item_id}[/bold] · {item.title}           "
            f"[[{state_color}]{item.state}[/{state_color}]]\n"
            f"[dim]{labels_str}{assignees_str}[/dim]"
        )
        self.query_one("#preview-header", Static).update(header)
        self.query_one("#preview-body", Markdown).update(item.body or "(no body)")
        self.query_one("#preview-comments-status", Static).update("[dim](loading comments…)[/dim]")
        self.query_one("#preview-comments", Static).update("")

    def update_comments(self, item: Item) -> None:
        if self._current_item is None or self._current_item.item_id != item.item_id:
            return
        self._current_item = item
        self.query_one("#preview-comments-status", Static).update("")
        if not item.comments:
            self.query_one("#preview-comments", Static).update("[dim](no comments)[/dim]")
            return

        parts: list[str] = []
        for c in item.comments:
            age = _format_age(c.created_at)
            parts.append(f"[bold]@{c.author}[/bold] · {age}\n\n{c.body}")
        self.query_one("#preview-comments", Static).update("\n\n---\n\n".join(parts))

    def show_comments_error(self, item_id: str, msg: str) -> None:
        if self._current_item is None or self._current_item.item_id != item_id:
            return
        self.query_one("#preview-comments-status", Static).update(
            f"[dim](failed to load comments: {msg})[/dim]"
        )


def _format_age(dt: datetime) -> str:
    now = datetime.now(UTC)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    diff = now - dt
    hours = int(diff.total_seconds() // 3600)
    if hours < 24:
        return f"{hours}h ago" if hours > 0 else "just now"
    days = hours // 24
    if days < 30:
        return f"{days}d ago"
    months = days // 30
    return f"{months}mo ago"
