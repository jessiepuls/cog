"""PreflightScreen — runs preflight checks inside a Textual modal."""

from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container
from textual.screen import ModalScreen
from textual.widgets import Footer, Header, Static

from cog.core.preflight import PreflightResult, run_checks
from cog.core.workflow import Workflow


class PreflightScreen(ModalScreen[bool]):
    BINDINGS = [
        Binding("q", "cancel", "Back"),
        Binding("escape", "cancel", "Back"),
    ]

    def __init__(self, workflow_cls: type[Workflow], project_dir: Path) -> None:
        super().__init__()
        self._workflow_cls = workflow_cls
        self._project_dir = project_dir

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("Running preflight checks…", id="preflight-status")
        yield Container(id="preflight-results")
        yield Footer()

    async def on_mount(self) -> None:
        results = await run_checks(self._workflow_cls.preflight_checks, self._project_dir)
        self._render_results(results)
        has_error = any(not r.ok and r.level == "error" for r in results)
        if not has_error:
            # Discard AwaitComplete — dismiss is fire-and-forget from a timer.
            def _dismiss() -> None:
                self.dismiss(True)

            self.set_timer(0.5, _dismiss)

    def _render_results(self, results: list[PreflightResult]) -> None:
        container = self.query_one("#preflight-results", Container)
        self.query_one("#preflight-status", Static).update("")
        for r in results:
            if r.ok:
                icon = "[green]✓[/green]"
            elif r.level == "error":
                icon = "[red]✗[/red]"
            else:
                icon = "[yellow]⚠[/yellow]"
            container.mount(Static(f"{icon}  {r.check}: {r.message}"))

    def action_cancel(self) -> None:
        self.dismiss(False)
