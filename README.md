# cog

A **harness** for running Claude Code on tracked issues. Handles
sandboxing, stage orchestration, state, and telemetry around a
**refine → ralph** workflow; ships with a Textual TUI and a headless
mode.

Cog doesn't call the Anthropic API directly — it wraps the `claude` CLI
in a Docker sandbox, parses its stream-json output, enforces timeouts,
captures cost + events, and drives a state machine across issue labels
and git branches. The refine + ralph workflows are one configuration of
that harness:

- **[`cog refine`](docs/workflows/refine.md)** — interactive chat that
  walks the decision tree on a `needs-refinement` issue, rewrites the
  body, and promotes it to `agent-ready`.
- **[`cog ralph`](docs/workflows/ralph.md)** — autonomous agent that
  picks an `agent-ready` issue, runs `build → review → document` inside
  a Docker sandbox, pushes the branch, opens the PR, waits for CI, and
  handles failures.

Status: early development. Dogfooded against its own repo; not
production-ready for unattended use elsewhere yet.

## Requirements

- Python 3.12+ (managed by `uv`)
- [`uv`](https://docs.astral.sh/uv/) — for install / dev setup
- `git`
- [`gh`](https://cli.github.com) (GitHub CLI, authenticated)
- [Docker](https://docs.docker.com/get-docker/) (daemon running)
- [Claude Code](https://docs.claude.com/claude-code) CLI
  (`claude` on `PATH`), authenticated — either via macOS keychain
  (default on macOS) or `ANTHROPIC_API_KEY`

## Install

Machine-wide, via `uv tool install`:

```bash
# HTTPS
uv tool install git+https://github.com/jessiepuls/cog.git

# or SSH
uv tool install git+ssh://git@github.com/jessiepuls/cog.git
```

After install, `cog` is on your `PATH`. Upgrade with
`uv tool upgrade cog`; uninstall with `uv tool uninstall cog`.

For development (work on cog itself), see
[CONTRIBUTING](docs/CONTRIBUTING.md).

## Quickstart

```bash
# Launch the TUI (default: dashboard view)
cog

# Headless: drain the agent-ready queue
cog ralph --loop --headless

# Specific item
cog refine --item 42
cog ralph --item 42

# Check preflight without launching a workflow
cog doctor
```

## TUI

Running `cog` with no args launches a Textual shell with three views:

| Shortcut | View | What you see |
|----------|------|--------------|
| `Ctrl+1` | Dashboard | Project status, queue counts, recent-runs strip, cost totals |
| `Ctrl+2` | Refine | `needs-refinement` queue → inline interview → inline review |
| `Ctrl+3` | Ralph | `agent-ready` queue → live log pane → completion panel |
| `Ctrl+Q` | Quit | Confirms if a workflow is in flight |

Workers persist across view switches — start a ralph run, flip to
refine mid-work, come back and the log is caught up. A yellow `●` on
a sidebar row indicates that view needs attention (interview awaiting
reply, run complete, etc.).

## Commands

```
cog                      Launch the TUI
cog ralph [options]      Autonomous agent (see docs/workflows/ralph.md)
cog refine [options]     Interactive refinement (see docs/workflows/refine.md)
cog doctor               Run preflight checks and exit
```

### `cog ralph`

| Flag | Description |
|------|-------------|
| `--item N` | Skip selection; run on issue number N |
| `--loop` | Queue-drain mode |
| `--max-iterations N` | Stop after N iterations (implies `--loop`) |
| `--headless` | Bypass Textual; stream events to stderr |
| `--restart` | Delete and recreate `cog/N-*` branch instead of resuming |
| `--project-dir PATH` | Project directory (default: cwd) |

### `cog refine`

| Flag | Description |
|------|-------------|
| `--item N` | Skip selection; run on issue number N |
| `--project-dir PATH` | Project directory (default: cwd) |

Refine requires the TUI (no `--headless`).

### `cog doctor`

| Flag | Description |
|------|-------------|
| `--project-dir PATH` | Directory to run checks from (default: cwd) |

Exits non-zero if any error-level preflight check fails.

## Docs

- **[Ralph workflow](docs/workflows/ralph.md)** — stages, outcomes,
  labels, fix-on-CI retry, branch resume
- **[Refine workflow](docs/workflows/refine.md)** — interview →
  rewrite → review, keybindings, reports
- **[Architecture](docs/ARCHITECTURE.md)** — harness internals, seams,
  state & telemetry, full environment-variable reference, extension
  guide
- **[Contributing](docs/CONTRIBUTING.md)** — dev setup, test / lint /
  type commands, conventions

## State directory

Cog writes per-project state under
`$XDG_STATE_HOME/cog/<project-slug>/` (default:
`~/.local/state/cog/<project-slug>/`):

- `state.json` — processed / deferred item tracking
- `runs.jsonl` — one telemetry record per run (schema in
  [ARCHITECTURE.md](docs/ARCHITECTURE.md#telemetry-runsjsonl))
- `reports/<ts>-<workflow>-<item-slug>.md` — per-run markdown report
