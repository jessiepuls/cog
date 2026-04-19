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

## Telemetry

Each run appends one JSON line to `<state-dir>/runs.jsonl` (default: `~/.local/share/cog/runs.jsonl`).

Key fields:

| Field | Type | Description |
|-------|------|-------------|
| `ts` | string | ISO-8601 UTC timestamp |
| `outcome` | string | `success`, `error`, `no-op`, `push-failed`, `deferred-by-blocker` |
| `stages` | array | Per-stage entry with `stage`, `duration_s`, `cost_usd`, `exit_status`, `commits` |
| `total_cost_usd` | float | Sum of stage costs |
| `duration_seconds` | float | Wall time across all stages |
| `error` | string\|null | `"stage 'X' failed \| cause=RunnerStalledError: ..."` on failure |
| `cause_class` | string\|null | Exception class name of the underlying cause (e.g. `RunnerStalledError`, `RunnerTimeoutError`) |
| `pr_url` | string\|null | PR URL if one was opened |

`cause_class` is populated when a stage fails with a classifiable runner error — useful for filtering
retry-eligible failures (`RunnerStalledError`, `RunnerTimeoutError`) from logic errors in telemetry queries.

When a stage fails after the runner has done real work (e.g. committed code), the partial result is
preserved: `stages` will contain an entry for the failed stage with accurate `duration_s`, `cost_usd`,
and `commits` rather than zeroes.

## Development

    uv sync
    uv run cog --help

Requires Python 3.12+ (managed automatically by uv).
