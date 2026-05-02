"""Dynamic slot registry for parallel workflow runs (#192).

A slot = one active workflow run: a sidebar entry + a view widget.
Slot key is (workflow, item_id) — same item can't have two runs of
the same workflow simultaneously, but refine+implement can coexist.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

SlotState = Literal["running", "awaiting_dismiss", "closed"]
SlotWorkflow = Literal["refine", "implement"]

_WORKFLOW_PREFIX: dict[str, str] = {"refine": "R", "implement": "I"}
_STATE_DOT: dict[str, str] = {
    "running": "[green]●[/green]",
    "awaiting_dismiss": "[yellow]◐[/yellow]",
    "closed": "",
    "errored": "[red]✕[/red]",
}


def max_concurrent_implements() -> int:
    """Read COG_MAX_CONCURRENT_IMPLEMENTS; default 3, minimum 1."""
    raw = os.environ.get("COG_MAX_CONCURRENT_IMPLEMENTS", "3")
    try:
        return max(1, int(raw))
    except ValueError:
        return 3


@dataclass
class DynamicSlot:
    """One active workflow run tracked in the sidebar."""

    run_id: str
    workflow: SlotWorkflow
    item_id: str
    state: SlotState = field(default="running")
    stage: str = field(default="")
    errored: bool = field(default=False)

    @property
    def slot_key(self) -> tuple[str, str]:
        return (self.workflow, self.item_id)

    def sidebar_label(self, keybind: str) -> str:
        prefix = _WORKFLOW_PREFIX[self.workflow]
        stage = self.stage[:10] if self.stage else "…"
        if self.errored:
            dot = _STATE_DOT["errored"]
        else:
            dot = _STATE_DOT.get(self.state, "")
        kb = keybind.replace("ctrl+", "^")
        return f"[dim]{kb}[/dim] {prefix} #{self.item_id} {stage} {dot}"


class DynamicSlotRegistry:
    """Ordered registry of active dynamic slots.

    Fires ``on_change`` whenever the slot list or any slot's visible
    state changes, so the sidebar can re-render synchronously.
    """

    def __init__(self, on_change: Callable[[], None] | None = None) -> None:
        self._slots: list[DynamicSlot] = []
        self._on_change = on_change or (lambda: None)

    @staticmethod
    def new_run_id() -> str:
        return uuid.uuid4().hex[:8]

    def add(self, slot: DynamicSlot) -> None:
        self._slots.append(slot)
        self._on_change()

    def remove(self, run_id: str) -> None:
        self._slots = [s for s in self._slots if s.run_id != run_id]
        self._on_change()

    def get(self, workflow: str, item_id: str) -> DynamicSlot | None:
        return next(
            (s for s in self._slots if s.workflow == workflow and s.item_id == item_id),
            None,
        )

    def get_by_run_id(self, run_id: str) -> DynamicSlot | None:
        return next((s for s in self._slots if s.run_id == run_id), None)

    def active_count(self, workflow: SlotWorkflow | None = None) -> int:
        """Count slots not yet closed. Both running and awaiting_dismiss count."""
        slots = [s for s in self._slots if s.state != "closed"]
        if workflow is not None:
            slots = [s for s in slots if s.workflow == workflow]
        return len(slots)

    def update_state(self, run_id: str, state: SlotState, *, errored: bool = False) -> None:
        slot = self.get_by_run_id(run_id)
        if slot is not None:
            slot.state = state
            slot.errored = errored
            self._on_change()

    def update_stage(self, run_id: str, stage: str) -> None:
        slot = self.get_by_run_id(run_id)
        if slot is not None:
            slot.stage = stage
            self._on_change()

    @property
    def active_slots(self) -> list[DynamicSlot]:
        return [s for s in self._slots if s.state != "closed"]

    def __iter__(self):
        return iter(self._slots)

    def __len__(self) -> int:
        return len(self._slots)
