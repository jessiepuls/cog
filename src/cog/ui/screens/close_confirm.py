"""Close-issue confirmation modal.

Pushed by the IssuesView when the user invokes "close" (default `c`) on a
focused item. Confirms before calling tracker.close.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container
from textual.screen import ModalScreen
from textual.widgets import Static


class CloseConfirmScreen(ModalScreen[bool]):
    BINDINGS = [
        Binding("y", "confirm", "Yes, close"),
        Binding("n", "cancel", "No, keep open"),
        Binding("escape", "cancel", "Cancel"),
    ]

    DEFAULT_CSS = """
    CloseConfirmScreen {
        align: center middle;
    }
    CloseConfirmScreen > Container {
        padding: 1 2;
        border: thick $warning;
        width: 70;
        height: auto;
        background: $surface;
    }
    CloseConfirmScreen #close-body {
        height: auto;
    }
    """

    def __init__(self, item_id: str, title: str) -> None:
        super().__init__()
        self._item_id = item_id
        self._title = title

    def compose(self) -> ComposeResult:
        body = (
            f"[bold]Close issue #{self._item_id}?[/bold]\n"
            f"[dim]{self._title}[/dim]\n"
            "\n"
            "Close this issue?  [bold]y[/bold] yes   "
            "[bold]n[/bold] no   [bold]esc[/bold] cancel"
        )
        with Container():
            yield Static(body, id="close-body")

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)
