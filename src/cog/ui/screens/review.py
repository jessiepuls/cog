"""ReviewScreen — review proposed refine rewrite before applying."""

from __future__ import annotations

from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, ScrollableContainer
from textual.screen import ModalScreen
from textual.widgets import Footer, Header, Static

from cog.ui.editor import suspend_and_edit
from cog.workflows.refine import ReviewDecision, ReviewOutcome


class ReviewScreen(ModalScreen[ReviewOutcome]):
    BINDINGS = [
        Binding("a", "accept", "Accept"),
        Binding("e", "edit", "Edit"),
        Binding("q", "abandon", "Abandon"),
        Binding("escape", "abandon", "Abandon"),
    ]

    DEFAULT_CSS = """
    ReviewScreen {
        align: center middle;
    }
    #review-header-strip {
        height: 3;
        padding: 1;
        background: $surface;
        border-bottom: solid $primary;
    }
    #panes-container {
        layout: horizontal;
        height: 1fr;
    }
    #panes-container.vertical {
        layout: vertical;
    }
    .body-pane {
        width: 1fr;
        height: 1fr;
        border: solid $primary;
        padding: 1;
    }
    .pane-label {
        height: 1;
        text-style: bold;
        padding: 0 1;
    }
    """

    def __init__(
        self,
        original_title: str,
        original_body: str,
        proposed_title: str,
        proposed_body: str,
        tmp_dir: Path,
    ) -> None:
        super().__init__()
        self._original_title = original_title
        self._original_body = original_body
        self._proposed_title = proposed_title
        self._proposed_body = proposed_body
        self._tmp_dir = tmp_dir

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(self._title_strip_text(), id="review-header-strip")
        yield Container(
            ScrollableContainer(
                Static(self._original_body, id="original-body"),
                id="original-pane",
                classes="body-pane",
            ),
            ScrollableContainer(
                Static(self._proposed_body, id="proposed-body"),
                id="proposed-pane",
                classes="body-pane",
            ),
            id="panes-container",
        )
        yield Footer()

    def on_mount(self) -> None:
        self._apply_layout(self.size.width)

    def on_resize(self, event: object) -> None:
        import textual.events

        if isinstance(event, textual.events.Resize):
            self._apply_layout(event.size.width)

    def _apply_layout(self, width: int) -> None:
        container = self.query_one("#panes-container")
        if width < 100:
            container.add_class("vertical")
        else:
            container.remove_class("vertical")

    def _title_strip_text(self) -> str:
        if self._proposed_title == self._original_title:
            return f"Title: {self._original_title} [unchanged]  |  a accept  e edit  q abandon"
        return (
            f"Title: {self._original_title} → {self._proposed_title}"
            f"  |  a accept  e edit  q abandon"
        )

    def action_accept(self) -> None:
        self.dismiss(
            ReviewOutcome(
                decision=ReviewDecision.ACCEPT,
                final_body=self._proposed_body,
                final_title=self._proposed_title,
            )
        )

    def action_abandon(self) -> None:
        self.dismiss(
            ReviewOutcome(
                decision=ReviewDecision.ABANDON,
                final_body=self._proposed_body,
                final_title=self._proposed_title,
            )
        )

    async def action_edit(self) -> None:
        edited = await suspend_and_edit(self.app, self._proposed_body, self._tmp_dir)
        if edited is not None:
            self._proposed_body = edited
            self.query_one("#proposed-body", Static).update(edited)
