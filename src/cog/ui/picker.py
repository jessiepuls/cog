"""Textual item-picker screens and adapter."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.screen import ModalScreen
from textual.widgets import Footer, Header, Input, Label, ListItem, ListView

from cog.core.errors import TrackerError
from cog.core.item import Item
from cog.core.tracker import IssueTracker
from cog.state_paths import project_state_dir

_TITLE_MAX = 80


@dataclass(frozen=True)
class PickerHistory:
    """Aggregated prior-run info for a single item."""

    count: int
    workflow: str
    last_outcome: str
    total_cost_usd: float


def load_picker_history(project_dir: Path) -> dict[str, PickerHistory]:
    """Build a history map keyed by item_id from the project's runs.jsonl.

    Missing file → empty dict. Malformed lines are skipped.
    Multi-workflow items take the most recent run's workflow / outcome.
    """
    path = project_state_dir(project_dir) / "runs.jsonl"
    if not path.exists():
        return {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}

    # Accumulate in chronological order — the file is append-only.
    counts: dict[str, int] = {}
    totals: dict[str, float] = {}
    last_workflow: dict[str, str] = {}
    last_outcome: dict[str, str] = {}

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        item_id_raw = obj.get("item")
        if item_id_raw is None:
            continue
        key = str(item_id_raw)
        counts[key] = counts.get(key, 0) + 1
        totals[key] = totals.get(key, 0.0) + float(obj.get("total_cost_usd", 0.0) or 0.0)
        workflow = obj.get("workflow", "?")
        if isinstance(workflow, str):
            last_workflow[key] = workflow
        outcome = obj.get("outcome", "?")
        if isinstance(outcome, str):
            last_outcome[key] = outcome

    return {
        key: PickerHistory(
            count=counts[key],
            workflow=last_workflow.get(key, "?"),
            last_outcome=last_outcome.get(key, "?"),
            total_cost_usd=totals[key],
        )
        for key in counts
    }


def _format_history_badge(h: PickerHistory) -> str:
    return (
        f" [dim]\\[{h.workflow} ×{h.count}: last {h.last_outcome}, ${h.total_cost_usd:.2f}][/dim]"
    )


class PickerScreen(ModalScreen[Item | None]):
    BINDINGS = [Binding("q", "cancel", "Cancel")]

    def __init__(
        self,
        items: Sequence[Item],
        tracker: IssueTracker,
        *,
        history: Mapping[str, PickerHistory] | None = None,
    ) -> None:
        super().__init__()
        self._items = list(items)
        self._tracker = tracker
        self._history: Mapping[str, PickerHistory] = history or {}

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
                badge = ""
                hist = self._history.get(item.item_id)
                if hist is not None:
                    badge = _format_history_badge(hist)
                list_items.append(
                    ListItem(Label(f"#{item.item_id} — {title}{badge}"), id=f"pick-{i}")
                )
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

    def __init__(self, app: App, tracker: IssueTracker, *, project_dir: Path | None = None) -> None:
        self._app = app
        self._tracker = tracker
        self._project_dir = project_dir

    async def pick(self, items: Sequence[Item]) -> Item | None:
        history = load_picker_history(self._project_dir) if self._project_dir else {}
        return await self._app.push_screen_wait(PickerScreen(items, self._tracker, history=history))
