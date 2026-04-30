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

### macOS: silencing the Docker Desktop privacy prompt

On macOS Sequoia (15+), Docker Desktop's credential helper triggers a
"would like to access data from other apps" prompt on every container
start, regardless of cog. Cog can't suppress this directly — the prompt
fires from `docker-credential-desktop` before cog's argv runs. Cog's
credential refresh (#135) is a different code path and unrelated.

Three workarounds, cheapest first:

1. **Grant the access once in System Settings.** System Settings →
   Privacy & Security → Files and Folders → enable for
   `docker-credential-desktop` (and/or your terminal). Persists across
   runs.

2. **Disable Docker's credential store for your user.** Edit
   `~/.docker/config.json` and remove the `credsStore` key. Cog only
   uses local images, so no registry credentials are needed.

   Before:

   ```json
   {
     "auths": {},
     "credsStore": "desktop"
   }
   ```

   After:

   ```json
   {
     "auths": {}
   }
   ```

   Side effect: any `docker login` workflows you have elsewhere stop
   storing registry credentials in the keychain.

3. **Switch to a different container runtime** that doesn't have
   Docker Desktop's TCC integration — e.g.
   [colima](https://github.com/abiosoft/colima),
   [OrbStack](https://orbstack.dev/), or
   [podman](https://podman.io/). All expose a Docker-compatible socket,
   so cog runs unchanged. Bigger change, but addresses other Docker
   Desktop friction at the same time.

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

Running `cog` with no args launches a Textual shell with four views:

| Shortcut | View | What you see |
|----------|------|--------------|
| `Ctrl+1` | Dashboard | Project status, queue counts, recent-runs strip, cost totals |
| `Ctrl+2` | Refine | `needs-refinement` queue → inline interview → inline review |
| `Ctrl+3` | Ralph | `agent-ready` queue → live log pane → completion panel |
| `Ctrl+4` | Chat | Freeform multi-turn chat with Claude over the current project |
| `Ctrl+Q` | Quit | Confirms if a workflow is in flight |

Workers persist across view switches — start a ralph run, flip to
refine mid-work, come back and the log is caught up. A yellow `●` on
a sidebar row indicates that view needs attention (interview awaiting
reply, run complete, etc.). Refine and Ralph rows also show a dim
right-aligned queue count (items in their respective queues) so you
can see queue depth without opening the Dashboard.

## Commands

```
cog                      Launch the TUI
cog ralph [options]      Autonomous agent (see docs/workflows/ralph.md)
cog refine [options]     Interactive refinement (see docs/workflows/refine.md)
cog doctor               Run preflight checks and exit
cog auth refresh         Sync Claude Code credentials from macOS keychain
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

### `cog auth refresh`

Copies Claude Code credentials from the macOS keychain to
`~/.claude/.credentials.json`. Useful when the sandbox can't reach the
keychain directly.

No flags. Exits non-zero if the `security` binary is absent or the
keychain entry doesn't exist. A no-op when `ANTHROPIC_API_KEY` is set.

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

Ralph also writes into the **project directory**:

- `.cog/worktrees/<id>-<slug>/` — isolated git worktree per iteration.
  Created at iteration start, removed after push. A surviving directory
  indicates a stuck or crashed run; see
  [ralph.md § Worktrees](docs/workflows/ralph.md#worktrees).
