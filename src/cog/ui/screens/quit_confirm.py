"""Quit confirmation modal (#129).

Pushed by the shell when the user presses Ctrl+Q while one or more views
have in-flight workflows. Lists each busy description and warns that
quitting will cancel them.
"""

from __future__ import annotations

from collections.abc import Iterable

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container
from textual.screen import ModalScreen
from textual.widgets import Static


class QuitConfirmScreen(ModalScreen[bool]):
    BINDINGS = [
        Binding("y", "confirm", "Yes, quit"),
        Binding("n", "cancel", "No, stay"),
        Binding("escape", "cancel", "Cancel"),
    ]

    DEFAULT_CSS = """
    QuitConfirmScreen {
        align: center middle;
    }
    QuitConfirmScreen > Container {
        padding: 1 2;
        border: thick $warning;
        width: 70;
        height: auto;
        background: $surface;
    }
    QuitConfirmScreen #quit-body {
        height: auto;
    }
    """

    def __init__(self, busy_descriptions: Iterable[str]) -> None:
        super().__init__()
        self._descriptions = tuple(busy_descriptions)

    def compose(self) -> ComposeResult:
        lines = "\n".join(f"  • {d}" for d in self._descriptions)
        body = (
            "[bold]These workflows are in progress.[/bold]\n"
            "[dim]Quitting will cancel them.[/dim]\n"
            "\n"
            f"{lines}\n"
            "\n"
            "Quit anyway?  [bold]y[/bold] yes   "
            "[bold]n[/bold] no   [bold]esc[/bold] cancel"
        )
        with Container():
            yield Static(body, id="quit-body")

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)
