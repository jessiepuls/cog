"""build_and_run factory — assembles the full dependency stack and launches a workflow."""

import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from cog.core.context import ExecutionContext
from cog.core.errors import TrackerError
from cog.core.preflight import PreflightResult, print_results, run_checks
from cog.core.workflow import Workflow
from cog.headless import run_headless
from cog.runners.claude_cli import ClaudeCliRunner
from cog.runners.docker_sandbox import DockerSandbox
from cog.state import JsonFileStateCache
from cog.state_paths import project_state_dir
from cog.telemetry import TelemetryWriter
from cog.trackers.github import GitHubIssueTracker

if TYPE_CHECKING:
    from textual.app import App

    from cog.ui.screens.run import RunScreen


async def build_run_screen(
    workflow_cls: type[Workflow],
    project_dir: Path,
    app: "App",
    *,
    item_id: int | None = None,
) -> "RunScreen":
    """Assemble the full dep stack and return a RunScreen.

    Does NOT run preflight — caller handles that separately.
    """
    sandbox = DockerSandbox(project_dir=project_dir)
    runner = ClaudeCliRunner(sandbox)
    tracker = GitHubIssueTracker(project_dir)
    state_dir = project_state_dir(project_dir)
    cache = JsonFileStateCache(state_dir / "state.json")
    cache.load()
    from cog.hosts.github import GitHubGitHost

    host = GitHubGitHost(project_dir)
    if cache.was_corrupt() or cache.is_empty():
        await cache.recover_from_remote(tracker, host, workflow_cls.queue_label)
    telemetry = TelemetryWriter(state_dir)
    workflow = workflow_cls(runner=runner, tracker=tracker, host=host)  # type: ignore[call-arg]
    tmp_dir = Path(tempfile.mkdtemp(prefix="cog-"))
    ctx = ExecutionContext(
        project_dir=project_dir,
        tmp_dir=tmp_dir,
        state_cache=cache,
        headless=False,
        telemetry=telemetry,
    )
    ctx.app = app
    if item_id is not None:
        ctx.item = await tracker.get(str(item_id))
    if workflow_cls.needs_item_picker and ctx.item is None:
        from cog.ui.picker import TextualItemPicker

        ctx.item_picker = TextualItemPicker(app, tracker, project_dir=project_dir)
    from cog.ui.screens.run import RunScreen

    return RunScreen(workflow, ctx, loop=False, max_iterations=1)


async def build_and_run(
    workflow_cls: type[Workflow],
    project_dir: Path,
    *,
    item_id: int | None,
    loop: bool,
    headless: bool,
    max_iterations: int | None = None,
    restart: bool = False,
) -> int:
    from cog.diagnostics import install_asyncio_handler

    install_asyncio_handler()
    # 1. Preflight
    results: list[PreflightResult] = await run_checks(workflow_cls.preflight_checks, project_dir)
    print_results(results)
    if any(not r.ok and r.level == "error" for r in results):
        return 1

    # 2. Validate headless compatibility
    if headless and not workflow_cls.supports_headless:
        print(
            f"error: {workflow_cls.name} does not support --headless",
            file=sys.stderr,
        )
        return 2

    # 3. Assemble dependencies
    sandbox = DockerSandbox(project_dir=project_dir)
    runner = ClaudeCliRunner(sandbox)
    tracker = GitHubIssueTracker(project_dir)
    state_dir = project_state_dir(project_dir)
    cache = JsonFileStateCache(state_dir / "state.json")
    cache.load()
    from cog.hosts.github import GitHubGitHost

    host = GitHubGitHost(project_dir)
    if cache.was_corrupt() or cache.is_empty():
        await cache.recover_from_remote(tracker, host, workflow_cls.queue_label)
    telemetry = TelemetryWriter(state_dir)

    workflow = workflow_cls(runner=runner, tracker=tracker, host=host, restart=restart)  # type: ignore[call-arg]

    tmp_dir = Path(tempfile.mkdtemp(prefix="cog-"))
    ctx = ExecutionContext(
        project_dir=project_dir,
        tmp_dir=tmp_dir,
        state_cache=cache,
        headless=headless,
        telemetry=telemetry,
    )

    if item_id is not None:
        try:
            ctx.item = await tracker.get(str(item_id))
        except TrackerError as e:
            print(f"error: could not load item #{item_id}: {e}", file=sys.stderr)
            return 1

    if headless:
        # Scan for orphaned worktrees from prior crashes before iterating
        if hasattr(workflow, "_apply_orphan_scan"):
            await workflow._apply_orphan_scan(project_dir)
        return await run_headless(workflow, ctx, loop=loop, max_iterations=max_iterations)

    from cog.ui.app import run_textual

    return await run_textual(
        workflow, ctx, loop=loop, max_iterations=max_iterations, tracker=tracker
    )
