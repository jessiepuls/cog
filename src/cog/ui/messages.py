"""Shared Textual Message types posted between views and the shell.

Kept in a dependency-free module so view modules and the shell can both
import without a circular dependency.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from textual.message import Message

if TYPE_CHECKING:
    from cog.core.item import Item
    from cog.ui.dynamic_slots import SlotState


class ViewAttention(Message):
    """Posted by a view widget when it needs the user's attention.

    The shell records per-view attention, renders a dot on the matching
    sidebar row, and auto-clears when the user switches to that view.
    Messages posted while the target view is already active are ignored.
    """

    def __init__(self, view_id: str, reason: str = "") -> None:
        self.view_id = view_id
        self.reason = reason
        super().__init__()


class LaunchSlotRequest(Message):
    """Posted by IssuesView to request a dynamic slot launch.

    The shell handles this: dedup-check, cap-check, create slot + view.
    """

    def __init__(self, workflow: Literal["refine", "implement"], item: Item) -> None:
        self.workflow = workflow
        self.item = item
        super().__init__()


class SlotStateChanged(Message):
    """Posted by DynamicSlotView when its slot's state or stage changes."""

    def __init__(self, run_id: str, state: SlotState, stage: str, errored: bool = False) -> None:
        self.run_id = run_id
        self.state = state
        self.stage = stage
        self.errored = errored
        super().__init__()


class SlotDismissed(Message):
    """Posted by DynamicSlotView when the user dismisses it (Enter or review action)."""

    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        super().__init__()
