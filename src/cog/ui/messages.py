"""Shared Textual Message types posted between views and the shell.

Kept in a dependency-free module so view modules and the shell can both
import without a circular dependency.
"""

from __future__ import annotations

from textual.message import Message


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
