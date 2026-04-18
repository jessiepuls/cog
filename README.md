# cog

TUI for managing the refine → ralph workflow across GitHub issues.

Status: early development — not yet usable. See the open issues for the v1 plan.

## Commands

    cog                   Launch the main menu (TUI)
    cog ralph             Autonomous agent: picks next agent-ready issue, runs build/review/document, opens PR
    cog refine            Interactive: refine a needs-refinement issue and rewrite it

Options for `cog ralph`:

    --item N              Skip selection; run on issue number N
    --loop                Autonomous queue-drain mode
    --headless            Bypass Textual; log to stderr
    --project-dir PATH    Project directory (default: cwd)

Options for `cog refine`:

    --item N              Skip selection; run on issue number N
    --project-dir PATH    Project directory (default: cwd)

## Development

    uv sync
    uv run cog --help

Requires Python 3.12+ (managed automatically by uv).
