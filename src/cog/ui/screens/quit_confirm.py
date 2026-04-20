"""Quit confirmation modal (#129).

Pushed by the shell when the user presses Ctrl+Q while one or more views
have in-flight workflows. Lists each busy description so the user knows
what they'd be killing.
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
        width: 60;
        height: auto;
        background: $surface;
    }
    QuitConfirmScreen #quit-title {
        text-style: bold;
        height: 1;
        padding-bottom: 1;
    }
    QuitConfirmScreen #quit-list {
        height: auto;
        padding-bottom: 1;
    }
    QuitConfirmScreen #quit-prompt {
        height: 1;
        padding-top: 1;
        color: $text-muted;
    }
    """

    def __init__(self, busy_descriptions: Iterable[str]) -> None:
        super().__init__()
        self._descriptions = tuple(busy_descriptions)

    def compose(self) -> ComposeResult:
        lines = "\n".join(f"  • {d}" for d in self._descriptions)
        with Container():
            yield Static("Workflows still in progress:", id="quit-title")
            yield Static(lines, id="quit-list")
            yield Static("Quit anyway?  [y] yes   [n] no / esc", id="quit-prompt")

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)
