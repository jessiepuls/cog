"""CogApp — top-level Textual application."""

from pathlib import Path

from textual.app import App
from textual.screen import Screen

from cog.core.context import ExecutionContext
from cog.core.workflow import Workflow
from cog.ui.screens.run import RunScreen


class CogApp(App):
    CSS_PATH = "cog.tcss"

    def __init__(self, initial_screen: Screen) -> None:
        super().__init__()
        self._initial = initial_screen

    def on_mount(self) -> None:
        self.push_screen(self._initial)


async def run_textual(workflow: Workflow, ctx: ExecutionContext, *, loop: bool) -> int:
    """Single-run launcher. Loop semantics live in #16."""
    run_screen = RunScreen(workflow, ctx)
    app = CogApp(run_screen)
    await app.run_async()
    return 0 if run_screen._state in ("completed", "cancelled") else 1


async def _run_main_menu(project_dir: Path) -> None:
    from cog.trackers.github import GitHubIssueTracker
    from cog.ui import WORKFLOWS
    from cog.ui.screens.main_menu import MainMenuScreen

    tracker = GitHubIssueTracker(project_dir)

    def _run_screen_factory(workflow_cls: type[Workflow]) -> Screen:
        # Assembles the full stack for a main-menu-initiated run.
        # Full context construction happens in wire.py for real subcommand runs;
        # here we just sketch the shape — concrete wiring finalizes with #12/#18.
        raise NotImplementedError(
            "Main-menu run wiring not yet complete (#12/#18). "
            "Use `cog ralph` or `cog refine` subcommands instead."
        )

    _ = WORKFLOWS  # referenced for side-effect of import
    app = CogApp(MainMenuScreen(project_dir, tracker, run_screen_factory=_run_screen_factory))
    await app.run_async()
