"""CogApp — top-level Textual application."""

from pathlib import Path

from textual.app import App
from textual.screen import Screen

from cog.core.context import ExecutionContext
from cog.core.tracker import IssueTracker
from cog.core.workflow import Workflow
from cog.ui.screens.run import RunScreen


class CogApp(App):
    CSS_PATH = "cog.tcss"
    TITLE = "Cog"

    def __init__(self, initial_screen: Screen) -> None:
        super().__init__()
        self._initial = initial_screen

    def on_mount(self) -> None:
        self.push_screen(self._initial)


async def run_textual(
    workflow: Workflow,
    ctx: ExecutionContext,
    *,
    loop: bool,
    max_iterations: int | None = None,
    tracker: IssueTracker | None = None,
) -> int:
    run_screen = RunScreen(workflow, ctx, loop=loop, max_iterations=max_iterations)
    app = CogApp(run_screen)
    ctx.app = app
    if type(workflow).needs_item_picker:
        assert tracker is not None, (
            f"{type(workflow).__name__}.needs_item_picker=True requires a tracker"
        )
        from cog.ui.picker import TextualItemPicker

        ctx.item_picker = TextualItemPicker(app, tracker, project_dir=ctx.project_dir)
    # The refine workflow calls ctx.review_provider during post_stages.
    # In the CLI path, use the modal ReviewScreen.
    from cog.ui.screens.review import ModalReviewProvider

    ctx.review_provider = ModalReviewProvider(app)
    await app.run_async()
    return 0 if run_screen._state in ("completed", "cancelled") else 1


async def _run_main_menu(project_dir: Path) -> None:
    from cog.trackers.github import GitHubIssueTracker
    from cog.ui.screens.shell import CogShellScreen

    tracker = GitHubIssueTracker(project_dir)
    app = CogApp(CogShellScreen(project_dir, tracker))
    await app.run_async()
