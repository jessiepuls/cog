# cog

TUI for managing the refine → ralph workflow across GitHub issues.

Status: early development — not yet usable. See the open issues for the v1 plan.

## Commands

    cog                   Launch the main menu (TUI)
    cog ralph             Autonomous agent: picks next agent-ready issue, runs build/review/document/rebase, opens PR
    cog refine            Interactive: refine a needs-refinement issue and rewrite it

Options for `cog ralph`:

    --item N              Skip selection; run on issue number N
    --loop                Autonomous queue-drain mode
    --headless            Bypass Textual; log to stderr
    --project-dir PATH    Project directory (default: cwd)

Options for `cog refine`:

    --item N              Skip selection; run on issue number N
    --project-dir PATH    Project directory (default: cwd)

Without `--item`, `cog refine` loops through the `needs-refinement` queue
until it is drained or you cancel the picker. With `--item N`, it runs a
single iteration on that issue and exits.

### Refine workflow

Each iteration runs in three phases:

1. **Interview** — multi-turn chat in the Textual chat pane. Claude asks
   one question at a time; the user replies until Claude emits
   `<<interview-complete>>` or the user ends early.
2. **Rewrite** — non-interactive Claude call that translates the interview
   transcript into a rewritten issue body and title.
3. **Review** — `ReviewScreen` (modal) lets you accept, edit, or abandon
   the proposed rewrite before anything is applied to the tracker.

#### Interview keyboard bindings

| Key | Action |
|-----|--------|
| `Enter` | Submit reply (empty string is a valid reply) |
| `Shift+Enter` | Insert newline |
| `Escape` / `Ctrl+D` | End interview early |

#### ReviewScreen keyboard bindings

| Key | Action |
|-----|--------|
| `a` | Accept proposed rewrite — applies body + title, swaps labels |
| `e` | Open `$EDITOR` on the proposed body; resume on exit |
| `q` / `Escape` | Abandon — no body change; preserves `needs-refinement` |

Pressing `e` drops you into `$EDITOR` (falls back to `nano`, then `vi`).
Exiting the editor without saving returns you to the review prompt — it does
not trigger abandon. Press `q` explicitly to abandon.

#### Outcomes

- **Accept** — body + title updated on tracker; `needs-refinement` removed,
  `agent-ready` applied. If the interview ended early (user pressed Escape),
  `partially-refined` is also applied and the body includes a ⚠ warning line.
- **Abandon** — no label changes; a comment is posted on the issue explaining
  the rewrite was not applied. Re-run `cog refine --item N` to retry.

#### Reports

After each iteration (accept or abandon) a report is written to
`~/.local/state/cog/<slug>/reports/<ts>-refine-<item-slug>.md` containing
the full original body, the proposed/applied body, the full interview
transcript, and a stage cost table.

#### Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `COG_REFINE_INTERVIEW_MODEL` | `claude-sonnet-4-6` | Model for each interview turn |
| `COG_REFINE_REWRITE_MODEL` | `claude-opus-4-6` | Model for the rewrite stage |

## Telemetry

Each run appends one JSON line to `<state-dir>/runs.jsonl` (default: `~/.local/share/cog/runs.jsonl`).

Key fields:

| Field | Type | Description |
|-------|------|-------------|
| `ts` | string | ISO-8601 UTC timestamp |
| `outcome` | string | `success`, `error`, `no-op`, `push-failed`, `deferred-by-blocker`, `rebase-conflict`, `ci-failed` |
| `stages` | array | Per-stage entry with `stage`, `duration_s`, `cost_usd`, `exit_status`, `commits`; for `cog refine` the first entry is `"interview"` |
| `total_cost_usd` | float | Sum of stage costs |
| `duration_seconds` | float | Wall time across all stages |
| `error` | string\|null | `"stage 'X' failed \| cause=RunnerStalledError: ..."` on failure |
| `cause_class` | string\|null | Exception class name of the underlying cause (e.g. `RunnerStalledError`, `RunnerTimeoutError`) |
| `pr_url` | string\|null | PR URL if one was opened |
| `retry_count` | int | Number of CI-fix retries attempted this iteration (0 when no CI failures occurred) |
| `ci_failed_checks` | array | Deduplicated names of CI checks that failed across all retry attempts; empty when no failures |

`cause_class` is populated when a stage fails with a classifiable runner error — useful for filtering
retry-eligible failures (`RunnerStalledError`, `RunnerTimeoutError`, `RebaseUnresolvedError`) from logic errors in telemetry queries.
CI-specific values: `CiFixFailedError` (claude exited without committing — unreproducible or couldn't fix),
`CiRetryCapExhaustedError` (retry cap hit; all attempts failed).

When a stage fails after the runner has done real work (e.g. committed code), the partial result is
preserved: `stages` will contain an entry for the failed stage with accurate `duration_s`, `cost_usd`,
and `commits` rather than zeroes.

## Development

    uv sync
    uv run cog --help

Requires Python 3.12+ (managed automatically by uv).
