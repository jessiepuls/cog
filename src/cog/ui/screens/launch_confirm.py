"""Yes/no confirmation modal used for non-recommended launches and aborts (#192).

Originally added for confirming non-recommended `r`/`i` launches in the
Issues view (`r` on agent-ready, `i` on needs-refinement, or either on an
item with no workflow labels). Also reused by the dynamic slot view to
confirm aborts — the screen is generic over the message and yes-label.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container
from textual.screen import ModalScreen
from textual.widgets import Static


class LaunchConfirmScreen(ModalScreen[bool]):
    """Ask the user to confirm a yes/no choice. Returns True on Enter, False on Esc."""

    BINDINGS = [
        Binding("enter", "confirm", "Confirm"),
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

    def __init__(self, message: str, *, yes_label: str = "proceed") -> None:
        super().__init__()
        self._message = message
        self._yes_label = yes_label

    def compose(self) -> ComposeResult:
        body = (
            f"{self._message}\n\n"
            f"[bold]Enter[/bold] to {self._yes_label}   [bold]Esc[/bold] to cancel"
        )
        with Container():
            yield Static(body, id="confirm-body")

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)
