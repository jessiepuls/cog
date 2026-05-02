"""Confirmation modal for non-recommended workflow launches (#192).

Shown when the user presses `r` on an agent-ready item (not needs-refinement)
or `i` on a needs-refinement item (not agent-ready), or either on an item
with no workflow labels.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container
from textual.screen import ModalScreen
from textual.widgets import Static


class LaunchConfirmScreen(ModalScreen[bool]):
    """Ask the user to confirm a non-recommended workflow launch."""

    BINDINGS = [
        Binding("enter", "confirm", "Yes, launch"),
        Binding("escape", "cancel", "Cancel"),
    ]

    DEFAULT_CSS = """
    LaunchConfirmScreen {
        align: center middle;
    }
    LaunchConfirmScreen > Container {
        padding: 1 2;
        border: thick $warning;
        width: 60;
        height: auto;
        background: $surface;
    }
    LaunchConfirmScreen #confirm-body {
        height: auto;
    }
    """

    def __init__(self, message: str) -> None:
        super().__init__()
        self._message = message

    def compose(self) -> ComposeResult:
        body = f"{self._message}\n\n[bold]Enter[/bold] to proceed   [bold]Esc[/bold] to cancel"
        with Container():
            yield Static(body, id="confirm-body")

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)
