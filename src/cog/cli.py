import asyncio
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
    headless: bool = typer.Option(False, "--headless", help="Bypass Textual; log to stderr."),
    project_dir: Path | None = typer.Option(None, "--project-dir"),  # noqa: B008
) -> None:
    """Autonomous agent: picks next agent-ready issue, runs build/review/document, opens PR."""
    from cog.workflows.ralph import RalphWorkflow

    exit_code = asyncio.run(
        build_and_run(
            RalphWorkflow,
            project_dir or Path.cwd(),
            item_id=item,
            loop=loop,
            headless=headless,
        )
    )
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
            loop=False,
            headless=False,
        )
    )
    raise typer.Exit(exit_code)


main = app
