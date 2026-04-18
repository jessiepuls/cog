import asyncio
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


main = app
