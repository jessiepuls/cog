"""Widgets for the read-only Issues browser view (#189)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.events import Blur, Focus, Resize
from textual.message import Message
from textual.reactive import reactive
from textual.timer import Timer
from textual.widget import Widget
from textual.widgets import Input, Label, ListItem, ListView, Markdown, OptionList, Static
from textual.widgets.option_list import Option

from cog.core.item import Item
from cog.core.tracker import ItemListFilter

# Cog-relevant labels: always pinned in row rendering, never dropped.
_COG_LABELS = frozenset({"needs-refinement", "agent-ready", "agent-failed", "partially-refined"})


def _luminance(hex_color: str) -> float:
    """WCAG relative luminance from a 6-char hex color string."""
    r, g, b = (int(hex_color[i : i + 2], 16) / 255 for i in (0, 2, 4))

    def _channel(c: float) -> float:
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    return 0.2126 * _channel(r) + 0.7152 * _channel(g) + 0.0722 * _channel(b)


def _fg_for_bg(hex_color: str) -> str:
    """Return 'black' or 'white' for best contrast on the given hex background."""
    return "black" if _luminance(hex_color) > 0.179 else "white"


def _chip_markup(name: str, color: str, *, dim: bool = False) -> str:
    fg = _fg_for_bg(color)
    style = "dim " if dim else ""
    return f"[{style}on #{color}][{fg}] {name} [/{fg}][/{style}on #{color}]"


def _apply_filter(items: list[Item], filter: ItemListFilter) -> list[Item]:
    """Pure client-side filter. All criteria are AND-combined."""
    result = items

    if filter.labels:
        label_set = set(filter.labels)
        result = [i for i in result if label_set.issubset(set(i.labels))]

    if filter.assignee:
        if filter.assignee == "(unassigned)":
            result = [i for i in result if not i.assignees]
        else:
            result = [i for i in result if filter.assignee in i.assignees]

    if filter.search:
        query = filter.search.strip().lstrip("#")
        try:
            num = int(query)
            result = [
                i for i in result if i.item_id == str(num) or query.lower() in i.title.lower()
            ]
        except ValueError:
            lower = query.lower()
            result = [i for i in result if lower in i.title.lower()]

    if filter.state != "all":
        result = [i for i in result if i.state == filter.state]

    result = sorted(
        result,
        key=lambda i: (i.updated_at, int(i.item_id) if i.item_id.isdigit() else 0),
        reverse=True,
    )
    return result


class ChipInput(Widget, can_focus=True):
    """Inline chip-input widget: chips + live-filtered dropdown.

    Posts ChipInput.Changed when the chip set changes.
    """

    class Changed(Message):
        def __init__(self, sender: ChipInput, chips: tuple[str, ...]) -> None:
            super().__init__()
            self.chip_input = sender
            self.chips = chips

    BINDINGS = [
        Binding("escape", "close_dropdown", "Close", show=False),
        Binding("backspace", "remove_last_chip", "Remove", show=False),
    ]

    DEFAULT_CSS = """
    ChipInput {
        height: auto;
        layout: vertical;
        border: solid $primary;
    }
    ChipInput #chip-row {
        height: 1;
        layout: horizontal;
    }
    ChipInput Input {
        height: 1;
        border: none;
        background: transparent;
        padding: 0;
        width: 1fr;
    }
    ChipInput OptionList {
        max-height: 8;
        display: none;
        background: $surface;
        border: solid $primary;
    }
    ChipInput OptionList.open {
        display: block;
    }
    """

    chips: reactive[tuple[str, ...]] = reactive((), init=False)

    def __init__(
        self,
        options: list[tuple[str, str]],
        placeholder: str = "",
        id: str | None = None,
    ) -> None:
        super().__init__(id=id)
        # options: list of (name, color_hex)
        self._all_options = options
        self._placeholder = placeholder

    def compose(self) -> ComposeResult:
        with Horizontal(id="chip-row"):
            yield Label("", id="chips-label")
            yield Input(placeholder=self._placeholder, id="chip-input")
        yield OptionList(id="chip-options")

    def on_mount(self) -> None:
        self._refresh_option_list("")

    def _refresh_option_list(self, query: str) -> None:
        opt_list = self.query_one("#chip-options", OptionList)
        opt_list.clear_options()
        lower = query.lower()
        matched = [
            (name, color)
            for name, color in self._all_options
            if lower in name.lower() and name not in self.chips
        ]
        for name, color in matched:
            opt_list.add_option(Option(_chip_markup(name, color), id=name))

    def _refresh_chips_label(self) -> None:
        chips_markup = " ".join(_chip_markup(name, self._color_for(name)) for name in self.chips)
        try:
            self.query_one("#chips-label", Label).update(chips_markup)
        except Exception:  # noqa: BLE001
            pass

    def _color_for(self, name: str) -> str:
        return next((c for n, c in self._all_options if n == name), "cccccc")

    def watch_chips(self, chips: tuple[str, ...]) -> None:
        self._refresh_chips_label()
        self.post_message(self.Changed(self, chips))

    def on_input_changed(self, event: Input.Changed) -> None:
        event.stop()
        self._refresh_option_list(event.value)
        opt_list = self.query_one("#chip-options", OptionList)
        opt_list.add_class("open") if event.value else opt_list.remove_class("open")

    def on_input_focus(self, _: Focus) -> None:
        self._refresh_option_list("")
        self.query_one("#chip-options", OptionList).add_class("open")

    def on_input_blur(self, _: Blur) -> None:
        # Slight delay so OptionList.OptionSelected can fire first.
        opt_list = self.query_one("#chip-options", OptionList)
        self.set_timer(0.1, lambda: opt_list.remove_class("open"))

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        name = event.option.id or ""
        if name and name not in self.chips:
            self.chips = (*self.chips, name)
        inp = self.query_one("#chip-input", Input)
        inp.value = ""
        self._refresh_option_list("")

    def action_close_dropdown(self) -> None:
        self.query_one("#chip-options", OptionList).remove_class("open")
        self.query_one("#chip-input", Input).blur()

    def action_remove_last_chip(self) -> None:
        inp = self.query_one("#chip-input", Input)
        if inp.value:
            return
        if self.chips:
            self.chips = self.chips[:-1]

    def update_options(self, options: list[tuple[str, str]]) -> None:
        self._all_options = options
        self._refresh_option_list("")

    def clear(self) -> None:
        self.chips = ()


class IssueFilterRow(Widget):
    """Always-visible filter bar at the top of the Issues view."""

    class FilterChanged(Message):
        def __init__(self, sender: IssueFilterRow, filter: ItemListFilter) -> None:
            super().__init__()
            self.filter_row = sender
            self.filter = filter

    DEFAULT_CSS = """
    IssueFilterRow {
        height: auto;
        padding: 0 1;
        border-bottom: solid $primary;
    }
    IssueFilterRow #filter-row-1 {
        layout: horizontal;
        height: auto;
    }
    IssueFilterRow #filter-labels-wrap {
        width: auto;
        height: auto;
        layout: horizontal;
    }
    IssueFilterRow #filter-assignee-wrap {
        width: auto;
        height: auto;
        layout: horizontal;
        margin-left: 2;
    }
    IssueFilterRow #filter-closed-wrap {
        width: auto;
        height: auto;
        layout: horizontal;
        margin-left: 2;
    }
    IssueFilterRow .filter-label {
        width: auto;
        height: 1;
        color: $text-muted;
    }
    IssueFilterRow #filter-row-2 {
        layout: horizontal;
        height: auto;
        margin-top: 0;
    }
    IssueFilterRow #search-label {
        width: auto;
        height: 1;
        color: $text-muted;
    }
    IssueFilterRow #filter-search {
        height: 1;
        border: none;
        background: transparent;
        width: 1fr;
    }
    IssueFilterRow #closed-toggle {
        height: 1;
        width: auto;
        color: $text-muted;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self._show_closed = False
        self._search_timer: Timer | None = None

    def compose(self) -> ComposeResult:
        with Horizontal(id="filter-row-1"):
            with Horizontal(id="filter-labels-wrap"):
                yield Label("Labels: ", classes="filter-label")
                yield ChipInput([], placeholder="filter…", id="filter-labels")
            with Horizontal(id="filter-assignee-wrap"):
                yield Label("Assignee: ", classes="filter-label")
                yield ChipInput([], placeholder="filter…", id="filter-assignee")
            with Horizontal(id="filter-closed-wrap"):
                yield Label("[□ closed]", id="closed-toggle")
        with Horizontal(id="filter-row-2"):
            yield Label("Search: ", id="search-label")
            yield Input(placeholder="title or #number…", id="filter-search")

    def _emit_filter(self) -> None:
        labels_ci = self.query_one("#filter-labels", ChipInput)
        assignee_ci = self.query_one("#filter-assignee", ChipInput)
        search_val = self.query_one("#filter-search", Input).value.strip() or None
        assignee: str | None = assignee_ci.chips[0] if assignee_ci.chips else None
        state: Literal["open", "closed", "all"] = "all" if self._show_closed else "open"
        f = ItemListFilter(
            labels=labels_ci.chips,
            state=state,
            assignee=assignee,
            search=search_val,
        )
        self.post_message(self.FilterChanged(self, f))

    def on_chip_input_changed(self, event: ChipInput.Changed) -> None:
        event.stop()
        self._emit_filter()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "filter-search":
            return
        event.stop()
        if self._search_timer is not None:
            try:
                self._search_timer.stop()
            except Exception:  # noqa: BLE001
                pass
        self._search_timer = self.set_timer(0.2, self._emit_filter)

    def toggle_closed(self) -> None:
        self._show_closed = not self._show_closed
        label = self.query_one("#closed-toggle", Label)
        label.update("[☑ closed]" if self._show_closed else "[□ closed]")
        self._emit_filter()

    def clear_all(self) -> None:
        self.query_one("#filter-labels", ChipInput).clear()
        self.query_one("#filter-assignee", ChipInput).clear()
        self.query_one("#filter-search", Input).value = ""
        if self._show_closed:
            self.toggle_closed()
        else:
            self._emit_filter()

    def update_label_options(self, options: list[tuple[str, str]]) -> None:
        self.query_one("#filter-labels", ChipInput).update_options(options)

    def update_assignee_options(self, options: list[tuple[str, str]]) -> None:
        self.query_one("#filter-assignee", ChipInput).update_options(options)

    def focus_search(self) -> None:
        self.query_one("#filter-search", Input).focus()

    @property
    def show_closed(self) -> bool:
        return self._show_closed


def _row_text(item: Item, width: int = 80) -> str:
    """Render a single-line row for IssueList."""
    num = f"#{item.item_id:<4}"
    cog = [lbl for lbl in item.labels if lbl in _COG_LABELS]
    other = [lbl for lbl in item.labels if lbl not in _COG_LABELS]
    has_failed = "agent-failed" in item.labels
    glyph = " ⚠" if has_failed else ""

    label_parts = cog + other
    chips_str = " ".join(f"[{lbl}]" for lbl in label_parts) + glyph

    # Account for number (6), space, chips, trailing spaces
    title_budget = max(10, width - 6 - len(chips_str) - 2)
    title = item.title if len(item.title) <= title_budget else item.title[: title_budget - 1] + "…"

    dim = item.state == "closed"
    if dim:
        return f"[dim]{num} [strike]{title}[/strike]  {chips_str}[/dim]"
    return f"{num} {title}  {chips_str}"


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
        for item in self._items:
            await lv.append(ListItem(Label(_row_text(item, width)), id=f"issue-{item.item_id}"))
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
