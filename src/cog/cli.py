import asyncio
import sys
from pathlib import Path

import typer

from cog import __version__
from cog.ui.wire import build_and_run

app = typer.Typer()


def _version_callback(value: bool) -> None:
    if value:
        print(f"cog {__version__}")
        raise typer.Exit()


@app.callback(invoke_without_command=True)
def callback(
    ctx: typer.Context,
    version: bool = typer.Option(False, "--version", callback=_version_callback, is_eager=True),
) -> None:
    """cog — TUI for managing refine → ralph workflows."""
    if ctx.invoked_subcommand is None:
        from cog.ui.app import _run_main_menu

        asyncio.run(_run_main_menu(Path.cwd()))


@app.command()
def ralph(
    item: int | None = typer.Option(None, "--item", help="Skip selection; run on this issue."),
    loop: bool = typer.Option(False, "--loop", help="Autonomous queue-drain mode (#16)."),
    max_iterations: int | None = typer.Option(
        None,
        "--max-iterations",
        help="Stop after N iterations (implies --loop).",
    ),
    headless: bool = typer.Option(False, "--headless", help="Bypass Textual; log to stderr."),
    restart: bool = typer.Option(
        False,
        "--restart",
        help="Delete and recreate any existing cog/N-* branch instead of resuming.",
    ),
    project_dir: Path | None = typer.Option(None, "--project-dir"),  # noqa: B008
) -> None:
    """Autonomous agent: picks next agent-ready issue, runs build/review/document, opens PR."""
    from cog.workflows.ralph import RalphWorkflow

    if max_iterations is not None:
        loop = True  # --max-iterations N implies --loop

    try:
        exit_code = asyncio.run(
            build_and_run(
                RalphWorkflow,
                project_dir or Path.cwd(),
                item_id=item,
                loop=loop,
                max_iterations=max_iterations,
                headless=headless,
                restart=restart,
            )
        )
    except KeyboardInterrupt:
        sys.stderr.write("\naborted.\n")
        exit_code = 130
    raise typer.Exit(exit_code)


@app.command()
def refine(
    item: int | None = typer.Option(None, "--item"),
    project_dir: Path | None = typer.Option(None, "--project-dir"),  # noqa: B008
) -> None:
    """Interactive: grill the user about a needs-refinement issue and rewrite it."""
    from cog.workflows.refine import RefineWorkflow

    exit_code = asyncio.run(
        build_and_run(
            RefineWorkflow,
            project_dir or Path.cwd(),
            item_id=item,
            loop=item is None,
            headless=False,
        )
    )
    raise typer.Exit(exit_code)


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


main = app
