"""Textual item-picker screens and adapter."""

from collections.abc import Sequence

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.screen import ModalScreen
from textual.widgets import Footer, Header, Input, Label, ListItem, ListView

from cog.core.errors import TrackerError
from cog.core.item import Item
from cog.core.tracker import IssueTracker

_TITLE_MAX = 80


class PickerScreen(ModalScreen[Item | None]):
    BINDINGS = [Binding("q", "cancel", "Cancel")]

    def __init__(self, items: Sequence[Item], tracker: IssueTracker) -> None:
        super().__init__()
        self._items = list(items)
        self._tracker = tracker

    def compose(self) -> ComposeResult:
        yield Header()
        list_items = []
        if not self._items:
            list_items.append(
                ListItem(
                    Label('[dim]No items in queue — select "Other" to enter an issue number[/dim]'),
                    id="pick-empty",
                    disabled=True,
                )
            )
        else:
            for i, item in enumerate(self._items):
                title = (
                    item.title
                    if len(item.title) <= _TITLE_MAX
                    else item.title[: _TITLE_MAX - 1] + "…"
                )
                list_items.append(ListItem(Label(f"#{item.item_id} — {title}"), id=f"pick-{i}"))
        list_items.append(ListItem(Label("Other — enter item number"), id="pick-other"))
        yield ListView(*list_items, id="picker-list")
        yield Footer()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        chosen_id = event.item.id or ""
        if chosen_id == "pick-empty":
            return
        if chosen_id == "pick-other":
            self._pick_other()
            return
        idx = int(chosen_id.removeprefix("pick-"))
        self.dismiss(self._items[idx])

    @work
    async def _pick_other(self) -> None:
        other = await self.app.push_screen_wait(OtherInputScreen(self._tracker))
        self.dismiss(other)

    def action_cancel(self) -> None:
        self.dismiss(None)


class OtherInputScreen(ModalScreen[Item | None]):
    BINDINGS = [
        Binding("q", "cancel", "Cancel"),
        Binding("escape", "cancel", "Cancel"),
    ]

    def __init__(self, tracker: IssueTracker) -> None:
        super().__init__()
        self._tracker = tracker

    def compose(self) -> ComposeResult:
        yield Header()
        yield Label("Enter issue number:")
        yield Input(id="other-input", placeholder="42")
        yield Label("", id="other-error")
        yield Footer()

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        raw = event.value.strip().lstrip("#")
        if not raw.isdigit():
            self.query_one("#other-error", Label).update(f"Invalid: {raw!r} is not a number.")
            return
        try:
            item = await self._tracker.get(raw)
        except TrackerError as e:
            self.query_one("#other-error", Label).update(f"Could not fetch #{raw}: {e}")
            event.input.value = ""
            event.input.focus()
            return
        self.dismiss(item)

    def action_cancel(self) -> None:
        self.dismiss(None)


class TextualItemPicker:
    """ItemPicker Protocol satisfier for Textual mode."""

    def __init__(self, app: App, tracker: IssueTracker) -> None:
        self._app = app
        self._tracker = tracker

    async def pick(self, items: Sequence[Item]) -> Item | None:
        return await self._app.push_screen_wait(PickerScreen(items, self._tracker))
