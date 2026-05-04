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

    issues_filter_query: str = "state:open"
    current_user_login: str | None = None

    def __init__(self, initial_screen: Screen, project_dir: Path) -> None:
        super().__init__()
        self._initial = initial_screen
        self.sub_title = project_dir.resolve().name

    def on_mount(self) -> None:
        self.push_screen(self._initial)
        self.run_worker(self._resolve_current_user(), exclusive=False, group="resolve-user")

    async def _resolve_current_user(self) -> None:
        import asyncio
        from subprocess import PIPE

        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                "gh",
                "api",
                "user",
                "--jq",
                ".login",
                stdout=PIPE,
                stderr=PIPE,
            )
            stdout, _ = await proc.communicate()
            if proc.returncode == 0:
                login = stdout.decode().strip()
                if login:
                    self.current_user_login = login
        except asyncio.CancelledError:
            if proc is not None and proc.returncode is None:
                proc.kill()
                await proc.wait()
            raise
        except Exception:  # noqa: BLE001
            pass


async def run_textual(
    workflow: Workflow,
    ctx: ExecutionContext,
    *,
    loop: bool,
    max_iterations: int | None = None,
    tracker: IssueTracker | None = None,
) -> int:
    from cog.diagnostics import patch_app_exit

    run_screen = RunScreen(workflow, ctx, loop=loop, max_iterations=max_iterations)
    app = CogApp(run_screen, ctx.project_dir)
    patch_app_exit(app)
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
    from cog.diagnostics import install_asyncio_handler, patch_app_exit
    from cog.trackers.github import GitHubIssueTracker
    from cog.ui.screens.shell import CogShellScreen

    install_asyncio_handler()
    tracker = GitHubIssueTracker(project_dir)
    app = CogApp(CogShellScreen(project_dir, tracker), project_dir)
    patch_app_exit(app)
    await app.run_async()
