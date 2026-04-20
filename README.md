# cog

A harness for running Claude Code on tracked issues. Handles sandboxing,
stage orchestration, state, and telemetry around a **refine → ralph**
workflow; ships with a Textual TUI and a headless mode.

Cog doesn't call the Anthropic API directly — it wraps the `claude` CLI
in a Docker sandbox, parses its stream-json output, enforces timeouts,
captures cost + events, and drives a state machine across issue labels
and git branches. The refine + ralph workflows are one configuration of
that harness:

- **`cog refine`** — interactive chat that walks the decision tree on a
  `needs-refinement` issue, rewrites the body, and promotes it to
  `agent-ready`.
- **`cog ralph`** — autonomous agent that picks an `agent-ready` issue,
  runs `build → review → document` inside a Docker sandbox, pushes the
  branch, opens the PR, waits for CI, and handles failures.

Status: early development. Dogfooded against its own repo; not
production-ready for unattended use elsewhere yet.

## Requirements

- Python 3.12+ (managed by `uv`)
- `git`
- [`gh`](https://cli.github.com) (GitHub CLI, authenticated)
- [Docker](https://docs.docker.com/get-docker/) (daemon running)
- [Claude Code](https://docs.claude.com/claude-code) CLI
  (`claude` on `PATH`), authenticated — either via macOS keychain
  (default on macOS) or `ANTHROPIC_API_KEY`

## Install

```bash
git clone https://github.com/jessiepuls/cog.git
cd cog
uv sync
```

## Quickstart

```bash
# In any cog-managed project's git worktree:
uv run cog                     # launch TUI
uv run cog ralph --loop        # headless: drain the agent-ready queue
uv run cog refine --item 42    # refine a specific issue
uv run cog doctor              # run preflight checks and exit
```

## Commands

### `cog`

Launches the Textual TUI. The main menu lists workflows with live queue
counts; pick one and press Enter to run preflight → picker → run screen.

| Flag | Description |
|------|-------------|
| `--version` | Print version and exit |

### `cog ralph`

Autonomous agent workflow.

| Flag | Description |
|------|-------------|
| `--item N` | Skip selection; run on issue number N |
| `--loop` | Queue-drain mode — iterate until queue is empty |
| `--max-iterations N` | Stop after N iterations (implies `--loop`) |
| `--headless` | Bypass Textual; stream stage events to stderr |
| `--restart` | Delete and recreate `cog/N-*` branch instead of resuming |
| `--project-dir PATH` | Project directory (default: cwd) |

### `cog refine`

Interactive refinement workflow.

| Flag | Description |
|------|-------------|
| `--item N` | Skip selection; run on issue number N |
| `--project-dir PATH` | Project directory (default: cwd) |

Without `--item`, `cog refine` loops through the `needs-refinement`
queue until it drains or you cancel the picker. With `--item N` it runs
once on that issue and exits. Requires the TUI (no `--headless`).

### `cog doctor`

Runs all preflight checks and prints the results. Exits non-zero if any
error-level check fails.

| Flag | Description |
|------|-------------|
| `--project-dir PATH` | Directory to run checks from (default: cwd) |

## Workflows

### Ralph

Autonomous agent. Queue label: `agent-ready`. Supports `--headless`.

**Iteration**:

1. **Select** the next `agent-ready` item assigned to you, sorted by
   priority tier (`pN` label) then creation date. Skip items whose
   blockers (`blocked by #X` / `depends on #X` in body or comments) are
   still open; mark them deferred in local state.
2. **Prepare branch**. Fetch origin, fast-forward the default branch,
   check out or create `cog/N-<slug>`. If the branch exists with
   unpushed commits, resume; otherwise restart. `--restart` forces
   recreation.
3. **Stages** (in order):
   - `build` (`claude-sonnet-4-6`) — implement the change + write tests.
   - `review` (`claude-opus-4-7`) — review the build output, fix issues.
   - `document` (`claude-sonnet-4-6`) — update docs / comments.
     Failures here don't abort the iteration; they're reported in the PR.
4. **Rebase** onto origin's default branch if the work branch is behind.
   Uses a separate Claude call with a rebase prompt to resolve conflicts.
   Unresolved conflicts abandon the iteration with `rebase-conflict`.
5. **Push and open PR**. If the branch already has a PR, update it.
6. **Wait for CI**. Poll until all required checks finish or the timeout
   expires. On green: remove `agent-ready`. On red: run a
   fix-on-CI-failure loop (reproduce → fix → push → wait) up to the
   retry cap; hand off with `agent-failed` if unresolvable.

**Outcomes**:

| Outcome | What it means |
|---------|--------------|
| `success` | PR opened (or updated) and CI passed |
| `no-op` | Claude exited without committing — e.g. nothing to change |
| `error` | Stage raised an unhandled exception |
| `push-failed` | Could not push the branch to origin |
| `rebase-conflict` | Claude's rebase stage could not resolve conflicts |
| `ci-failed` | CI failed and the retry cap was exhausted |
| `deferred-by-blocker` | Item had open blockers; skipped this iteration |

**Label lifecycle** (additive, not destructive):

- `agent-ready` — queue label; removed on `success`, `no-op`, or
  `ci-failed` (hand-off). Kept on `error` so the item remains eligible
  for resume.
- `agent-failed` — added on any failure path (`error`, `push-failed`,
  `rebase-conflict`, `ci-failed`). Removed when a subsequent run
  succeeds.
- `agent-abandoned` — added on `no-op`.

### Refine

Interactive. Queue label: `needs-refinement`. TUI only.

Each iteration runs in three phases:

1. **Interview** — multi-turn chat in the Textual chat pane. Claude asks
   one question at a time, always with a recommended answer; you reply,
   push back, or pick an alternative. Claude walks the decision tree
   (architecture, behavior, error paths, scope, failure modes, testing)
   until every branch has a concrete answer, then emits
   `<<interview-complete>>`. You can end early with Escape / Ctrl+D.
2. **Rewrite** — non-interactive Claude call that crystallizes the
   interview decisions into an implementation specification (title +
   body), intended to be detailed enough that a headless agent can ship
   without asking clarifying questions.
3. **Review** — `ReviewScreen` (modal) lets you accept, edit, or abandon
   the proposed rewrite before anything is applied to the tracker.

#### Interview keybindings

| Key | Action |
|-----|--------|
| `Enter` | Submit reply (empty is a valid reply) |
| `Shift+Enter` | Insert newline |
| `Escape` / `Ctrl+D` | End interview early |
| `Ctrl+I` | Show / hide the full item body |
| `Ctrl+C` | Cancel the whole workflow |

#### ReviewScreen keybindings

| Key | Action |
|-----|--------|
| `a` | Accept — applies body + title, swaps labels |
| `e` | Open `$EDITOR` on the proposed body; resume on exit |
| `q` / `Escape` | Abandon — no body change; preserves `needs-refinement` |

Pressing `e` drops you into `$EDITOR` (falls back to `nano`, then `vi`).
Exiting without saving returns to the review prompt — not abandon.

#### Outcomes

- **Accept** — body + title updated on tracker; `needs-refinement`
  removed, `agent-ready` applied. If the interview ended early,
  `partially-refined` is also applied and the body gets a ⚠ warning
  line.
- **Abandon** — no label changes; a comment explains the rewrite was
  not applied. Re-run `cog refine --item N` to retry.

## Preflight checks

Every workflow run starts with a preflight check bundle. Ralph runs all
checks; refine skips `clean_tree` and `default_branch` (refine doesn't
touch git).

| Check | Scope | What it verifies |
|-------|-------|-------------------|
| `host_tool.git` | both | `git` on PATH |
| `host_tool.gh` | both | `gh` on PATH |
| `host_tool.docker` | both | `docker` on PATH |
| `git_repo` | both | inside a git worktree |
| `clean_tree` | ralph | working tree has no staged / unstaged / untracked changes |
| `default_branch` | ralph | currently on the default branch |
| `origin_remote` | both | `origin` remote is configured |
| `gh_auth` | both | `gh auth status` passes |
| `gh_token_file` | both | gh token is file-based (not macOS keychain) |
| `docker_running` | both | Docker daemon is reachable |
| `claude_auth` | both | `ANTHROPIC_API_KEY` or macOS keychain entry present (warning) |

Run `cog doctor` to check without launching a workflow.

## Configuration

All configuration is via environment variables. Defaults are tuned for
dogfooding; override as needed.

### Models

| Variable | Default | Purpose |
|----------|---------|---------|
| `COG_REFINE_INTERVIEW_MODEL` | `claude-opus-4-7` | Refine interview turns |
| `COG_REFINE_REWRITE_MODEL` | `claude-opus-4-7` | Refine rewrite stage |
| `COG_RALPH_BUILD_MODEL` | `claude-sonnet-4-6` | Ralph build stage |
| `COG_RALPH_REVIEW_MODEL` | `claude-opus-4-7` | Ralph review stage |
| `COG_RALPH_DOCUMENT_MODEL` | `claude-sonnet-4-6` | Ralph document stage |

### Runner timeouts

| Variable | Default | Purpose |
|----------|---------|---------|
| `COG_RUNNER_TIMEOUT_SECONDS` | `1800` | Overall subprocess wall-clock limit |
| `COG_RUNNER_INACTIVITY_TIMEOUT_SECONDS` | `300` | Idle window with no stream events (Claude thinking between tool calls) |
| `COG_RUNNER_TOOL_CALL_TIMEOUT_SECONDS` | `600` | Per-tool-call limit (used while a tool is outstanding) |
| `COG_STREAM_LINE_LIMIT_BYTES` | `16777216` (16 MiB) | Max bytes per streamed JSON line |

### CI polling

| Variable | Default | Purpose |
|----------|---------|---------|
| `COG_CI_POLL_INTERVAL_SECONDS` | `15` | Interval between `gh pr checks` polls |
| `COG_CI_TIMEOUT_SECONDS` | `1800` | Total wait time before declaring CI timed out |
| `COG_CI_MAX_RETRIES` | `2` | Max fix-on-CI-failure retries before handing off |

### Misc

| Variable | Default | Purpose |
|----------|---------|---------|
| `XDG_STATE_HOME` | `~/.local/state` | Base directory for state files (XDG spec) |
| `EDITOR` | unset | Editor invoked by ReviewScreen's `e` binding (falls back to `nano`, then `vi`) |
| `ANTHROPIC_API_KEY` | unset | Required if not using the macOS keychain entry |

## State directory

Cog writes per-project state under
`$XDG_STATE_HOME/cog/<project-slug>/` (default:
`~/.local/state/cog/<project-slug>/`), where `<project-slug>` is the
project directory name with non-alphanumerics replaced by `-`.

| Path | Contents |
|------|----------|
| `state.json` | Processed / deferred item tracking. Processed entries are revived if the issue is edited (`updated_at > ts`). |
| `runs.jsonl` | One JSON line per workflow run — telemetry record (see below). |
| `reports/<ts>-<workflow>-<item-slug>.md` | Human-readable run report. Refine reports include the original body, proposed body, full interview transcript, and a stage cost table. |

## Telemetry

Each run appends one JSON line to `runs.jsonl`. Key fields:

| Field | Type | Description |
|-------|------|-------------|
| `ts` | string | ISO-8601 UTC timestamp |
| `cog_version` | string | Cog version that wrote the record |
| `project` | string | Project slug |
| `workflow` | string | `ralph` or `refine` |
| `item` | int | Issue number |
| `outcome` | string | See outcome table in [Ralph](#ralph) |
| `branch` | string \| null | Work branch name, if any |
| `pr_url` | string \| null | PR URL if one was opened |
| `duration_seconds` | float | Wall time across all stages |
| `total_cost_usd` | float | Sum of stage costs |
| `stages` | array | Per-stage entry: `stage`, `model`, `duration_s`, `cost_usd`, `exit_status`, `commits`, `input_tokens`, `output_tokens`. Refine records include an `interview` entry aggregating all interview turns. |
| `error` | string \| null | Formatted error message on failure |
| `cause_class` | string \| null | Exception class (e.g. `RunnerStalledError`, `RunnerTimeoutError`, `RebaseUnresolvedError`, `CiTimeoutError`, `CiFixFailedError`, `CiRetryCapExhaustedError`) |
| `resumed` | bool | Whether this iteration resumed an existing branch |
| `retry_count` | int | CI-fix retries attempted this iteration (0 if no failures) |
| `ci_failed_checks` | array | Deduplicated names of failed CI checks across all retries |

`cause_class` lets you filter retry-eligible failures (runner stalls /
timeouts) from logic errors when querying telemetry. When a stage fails
after doing real work (e.g. committed code), the partial result is
preserved in `stages` with accurate `duration_s`, `cost_usd`, and
`commits` rather than zeroes.

## Labels

Cog reads and writes the following labels. Missing labels are created on
first use with a description and color.

| Label | Used by | Meaning |
|-------|---------|---------|
| `agent-ready` | ralph, refine | Queue label for ralph. Added by refine on accept. |
| `needs-refinement` | refine | Queue label for refine. Removed by refine on accept. |
| `partially-refined` | refine | Interview ended early; body may be incomplete. |
| `agent-failed` | ralph | Additive failure signal. Removed on next success. |
| `agent-abandoned` | ralph | Added on `no-op` outcomes (Claude exited without committing). |
| `pN` (e.g. `p1`, `p2`) | ralph | Priority tier; lowest wins ordering. Items without `pN` sort last. |

`blocked by #N` and `depends on #N` in issue body / comments are parsed
by ralph for blocker tracking — no label involved.

## Architecture (contributors)

See [CLAUDE.md](CLAUDE.md) for a conceptual overview and invariants.

Summary:

- `core/` — abstract interfaces (Workflow, StageExecutor, IssueTracker,
  GitHost, Sandbox, AgentRunner, RunEvent, errors).
- `workflows/` — `RalphWorkflow`, `RefineWorkflow`.
- `runners/` — `ClaudeCliRunner` (invokes `claude` CLI with
  `stream-json` output and parses the event stream).
- `trackers/`, `hosts/` — `GitHubIssueTracker`, `GitHubGitHost`.
- `ui/` — Textual app: screens (`MainMenuScreen`, `RunScreen`,
  `ReviewScreen`, `PickerScreen`, `PreflightScreen`) and widgets
  (`ChatPaneWidget`, `LogPaneWidget`).
- `prompts/` — Markdown prompt templates loaded at runtime.
- `state.py`, `state_paths.py` — XDG state layout + JSON cache.
- `checks.py` — preflight check implementations.
- `telemetry.py` — `TelemetryRecord` + `TelemetryWriter`.

## Development

```bash
uv sync                                               # install deps
uv run pytest                                         # test suite
uv run mypy src                                       # type check
uv run ruff check . && uv run ruff format --check .   # lint + format check
```

Full test suite runs in ~22s. `pytest-asyncio` is in auto mode;
`filterwarnings = ["error"]` — warnings fail tests.

Sandbox integration tests are gated on `COG_INTEGRATION_TESTS=1` (they
require Docker).
