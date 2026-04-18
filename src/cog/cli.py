import asyncio
import sys
from pathlib import Path

import typer

from cog import __version__

app = typer.Typer(no_args_is_help=True)


def _version_callback(value: bool) -> None:
    if value:
        print(f"cog {__version__}")
        raise typer.Exit()


@app.callback()
def callback(
    version: bool = typer.Option(False, "--version", callback=_version_callback, is_eager=True),
) -> None:
    """cog — TUI for managing refine → ralph workflows."""


@app.command()
def doctor(
    project_dir: Path = typer.Option(  # noqa: B008
        None, "--project-dir", help="Directory to run checks from."
    ),
) -> None:
    """Run preflight checks against the current project and report."""
    from cog.checks import ALL_CHECKS
    from cog.core.preflight import print_results, run_checks

    resolved = project_dir or Path.cwd()
    results = asyncio.run(run_checks(ALL_CHECKS, resolved))
    print_results(results)
    if any(r.level == "error" and not r.ok for r in results):
        raise typer.Exit(1)


async def build_and_run(
    *,
    project_dir: Path,
    item_id: str | None,
    headless: bool,
) -> int:
    """Build workflow + context and dispatch to headless or TUI runner.

    Placeholder: real workflow/state wiring lands in #13 and #14.
    """
    # Real workflow/runner/state wiring lands in #13 and #14.
    # For now, reject non-headless runs and error out with a clear message.
    if not headless:
        sys.stderr.write("TUI mode not yet implemented; use --headless\n")
        return 1

    from cog.core.context import ExecutionContext
    from cog.headless import run_headless
    from cog.runners.claude_cli import ClaudeCliRunner
    from cog.runners.docker_sandbox import DockerSandbox
    from cog.state import JsonFileStateCache
    from cog.state_paths import project_state_dir
    from cog.workflows.dummy import DummyWorkflow

    state_path = project_state_dir(project_dir) / "state.json"
    state_cache = JsonFileStateCache(state_path)
    state_cache.load()

    sandbox = DockerSandbox()
    runner = ClaudeCliRunner(sandbox)
    workflow = DummyWorkflow(runner)
    ctx = ExecutionContext(
        project_dir=project_dir,
        tmp_dir=project_dir / ".cog" / "tmp",
        state_cache=state_cache,
        headless=headless,
    )
    return await run_headless(workflow, ctx)


@app.command()
def ralph(
    project_dir: Path = typer.Option(  # noqa: B008
        None,
        "--project-dir",
        help="Project directory to run against.",
    ),
    item_id: str = typer.Option(  # noqa: B008
        None,
        "--item",
        help="Item ID to process.",
    ),
    headless: bool = typer.Option(  # noqa: B008
        False,
        "--headless",
        help="Run without TUI (outputs to stderr).",
    ),
) -> None:
    """Run the ralph workflow (agent-ready issues)."""
    resolved = project_dir or Path.cwd()
    try:
        exit_code = asyncio.run(
            build_and_run(
                project_dir=resolved,
                item_id=item_id,
                headless=headless,
            )
        )
    except KeyboardInterrupt:
        sys.stderr.write("\naborted.\n")
        exit_code = 130
    raise typer.Exit(exit_code)


main = app
