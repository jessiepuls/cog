import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path

import typer

from cog import __version__
from cog.ui.wire import build_and_run

app = typer.Typer()
auth = typer.Typer()
app.add_typer(auth, name="auth")


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
    from cog.diagnostics import setup_diagnostics

    setup_diagnostics(Path.cwd())
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


@auth.command("refresh")
def auth_refresh() -> None:
    """Copy Claude Code credentials from the macOS keychain to ~/.claude/.credentials.json."""
    from cog.runners.docker_sandbox import ensure_credentials

    result = asyncio.run(ensure_credentials(force=True))

    if result == "api_key_set":
        print("ANTHROPIC_API_KEY is set; no keychain refresh needed")
        return

    if result == "security_missing":
        print("keychain refresh requires macOS 'security' binary", file=sys.stderr)
        raise typer.Exit(1)

    if result == "keychain_missing":
        print(
            "Claude Code credentials not found in keychain; log in via 'claude' first",
            file=sys.stderr,
        )
        raise typer.Exit(1)

    # result == "refreshed"
    print(_format_expiry_message())


def _format_expiry_message() -> str:
    creds_file = Path.home() / ".claude" / ".credentials.json"
    try:
        import json

        data = json.loads(creds_file.read_bytes())
        expires_at_ms = data["claudeAiOauth"]["expiresAt"]
        dt = datetime.fromtimestamp(expires_at_ms / 1000, tz=UTC).astimezone()
        return (
            f"refreshed credentials from keychain (expires {dt.strftime('%Y-%m-%d %H:%M:%S %Z')})"
        )
    except Exception:
        return "refreshed credentials from keychain"


main = app
