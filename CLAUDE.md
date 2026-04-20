# CLAUDE.md

This file is loaded into the context of every Claude Code session working
on this project. Keep it terse and high-signal.

## What cog is

A harness for running Claude Code on tracked issues. The "control plane"
around `claude` subprocesses: sandboxing (Docker), stage orchestration,
stream-json parsing, timeout / stall detection, state management
(processed/deferred items, branch lifecycle), telemetry, and a human
hand-off surface (Textual TUI or stderr in `--headless`).

The refine → ralph workflow is one configuration of that harness; the
harness itself is workflow-agnostic.

- **refine**: interactive. Interview the user about a `needs-refinement`
  item, rewrite the body, hand it off as `agent-ready`.
- **ralph**: autonomous. Pick an `agent-ready` item, run
  build → review → document stages inside the sandbox, push the branch,
  open the PR, wait for CI, handle failures.

Cog does not call the Anthropic API directly — it invokes the `claude`
CLI via `ClaudeCliRunner` with `--output-format stream-json` and parses
the event stream.

## Architecture

```
src/cog/
├── cli.py            Typer CLI entry point (cog, cog ralph, cog refine, cog doctor)
├── core/             Abstract interfaces: Workflow, StageExecutor, ExecutionContext,
│                     IssueTracker, GitHost, Sandbox, AgentRunner, RunEvent, errors
├── workflows/        RalphWorkflow, RefineWorkflow (and a DummyWorkflow for tests)
├── runners/          ClaudeCliRunner (invokes `claude` binary with stream-json output)
│                     SandboxRunner (wraps argv in docker exec)
├── trackers/         GitHubIssueTracker (via `gh` CLI)
├── hosts/            GitHubGitHost (PR create/update/merge, CI check state)
├── ui/               Textual TUI: app, screens/, widgets/
├── prompts/          Markdown prompt templates per stage (build.md, review.md, ...)
├── state.py          JsonFileStateCache (processed / deferred items)
├── state_paths.py    XDG-compliant state directory resolution
├── checks.py         Preflight checks + RALPH_CHECKS / REFINE_CHECKS bundles
├── telemetry.py      TelemetryRecord + TelemetryWriter (runs.jsonl)
└── loop.py           Cross-iteration state primitives
```

Two separate abstractions — don't conflate: **IssueTracker** (reads/writes
issues + comments + labels) and **GitHost** (branch / PR / CI operations).
Today both are GitHub-backed but the split is intentional for future
GitLab/Linear support. Keep tracker-agnostic code tracker-agnostic in
naming ("item" not "issue", "tracker" not "GitHub") outside `trackers/`
and `hosts/`.

## Dev commands

```bash
uv sync                                    # install deps
uv run pytest                              # test suite (~22s, 880+ tests)
uv run mypy src                            # type check
uv run ruff check .                        # lint
uv run ruff format --check .               # format check
uv run ruff format .                       # apply formatting
uv run cog --help                          # CLI help
```

Python 3.12+ (managed by uv). Build: hatchling. Test runner: pytest with
`pytest-asyncio` in auto mode. Warnings are errors (`filterwarnings`
strict).

## Key invariants

- **Event-driven UI.** Stages run in a subprocess; events (AssistantText,
  ToolUse, StageStart/End, ItemSelected, Status) flow through
  `ctx.event_sink` to whichever widget is mounted. Adding a new UI
  signal means adding an event type in `core/runner.py` and a handler
  in the widget's `emit()`.
- **StageExecutor is the single iteration unit.** A workflow iteration =
  `select_item → pre_stages → stages → post_stages → classify_outcome
  → finalize_{success|noop|error}`. Don't replicate this shape inline;
  extend the workflow interface.
- **Ralph failures are additive, not destructive.** On error, ralph
  keeps `agent-ready`, adds `agent-failed`. The item stays eligible for
  resume. `agent-failed` is a *signal*, not a terminal state.
- **Revival rule.** A processed item becomes eligible again when
  `item.updated_at > record.ts`. Users can re-queue by editing the
  issue; no label manipulation required.
- **Deferred items aren't labels.** Ralph parses `blocked by #N` /
  `depends on #N` from body + comments; defers via `state.json` until
  blockers close. No tracker-visible state change.
- **Refine runs only in Textual mode.** Requires an ItemPicker. Headless
  refine errors out by design (there's no way to do interactive chat
  without a UI).
- **`fresh_iteration_context` preserves `item` on iteration 1** when the
  caller pre-populated `base_ctx.item` (e.g. `--item N` or main-menu
  picker). See `src/cog/loop.py`.

## Testing conventions

- Most tests are async; pytest-asyncio auto mode is on.
- UI tests use Textual's `pilot.run_test(headless=True)`.
- Real subprocesses are faked via `FakeSubprocessRegistry` (see
  `tests/conftest.py` and `tests/test_trackers/conftest.py`). Don't call
  `gh` / `git` / `docker` directly from tests.
- For stage-executor tests, use `ctx_factory` and `echo_runner` fixtures
  from `tests/conftest.py` + `tests/fakes.py`.
- Integration tests that need Docker gate on `COG_INTEGRATION_TESTS=1`.
- When replacing `push_screen_wait` in UI tests, note that the fake
  bypasses Textual's worker-context check — any regression tests for
  "must run in a worker" need to assert dispatch (e.g. `run_worker`
  was called), not just the downstream flow.

### Test granularity

- **Parametrize** when inputs vary but the behavior being checked is the
  same. Keep separate tests when the *intent* differs — each test should
  have a name that explains one thing.
- **Group assertions** within a single test when they verify the same
  outcome (e.g., all fields of a returned object). Split into separate
  tests when each assertion tests a distinct, independently-breakable
  behavior.
- **Pick one abstraction level per behavior.** A unit test and an
  integration test for the exact same assertion is redundancy, not
  coverage. Test at the level that gives the clearest failure signal.
- **Signal that a test is over-testing**: if the test name ends with
  `_field_X` or reads like a field enumeration, consider grouping. If
  fixing one bug causes five tests to fail for the same reason, those
  five tests are likely one test written five ways.

## Prompts

Prompts live as markdown in `src/cog/prompts/claude/{ralph,refine}/*.md`
and are loaded via `importlib.resources` at runtime. Each stage has its
own file. When changing prompt behavior, change the markdown — not
Python strings.

### Prompt-writing conventions

**Prefer on-demand fetching over context injection.** For any content that
is large (>few KB), variable, or only partially consumed: give claude a
pointer + instruction, not the content itself.

Bad:
```
### Issue body
{{full item body interpolated here — 20KB}}
```

Good:
```
To see the item body, run `gh issue view {id} --json body,comments`.
Fetch when you need it; don't assume you need the full body for every decision.
```

Benefits: smaller prompts start faster, allow claude-code's context compaction
to drop unused content, leave headroom before stall classes emerge, and pick up
live state on retry.

Exceptions (keep injected): small, always-needed, stable context — item number,
item title, branch name.

**Bounded tool calls warning.** All ralph prompts warn claude about the >30KB
tool-output persistence behavior. Preserve that warning in any new prompt or
when updating existing ones.

**Structured final-message sections.** Build prompts tell claude to end with
`### Summary / ### Key changes / ### Test plan`. Never change these section
names without updating `_split_final_message` in `workflows/ralph.py` and
matching fixtures.

**Tracker-agnostic language.** Refer to "tracked items" not "GitHub issues" in
prompts and non-GitHub-specific code.

## Style / conventions

- **Tracker-agnostic language** outside `trackers/` and `hosts/`. Prefer
  "item" over "issue", "tracker" over "GitHub", "PR" is fine because
  hosts are always PR-shaped.
- **No comments that restate code.** Only comment WHY when non-obvious
  (a subtle invariant, a workaround, a reference to an incident).
- **Error handling at boundaries only.** Don't defensively validate
  inputs from internal code. Validate at: user input, subprocess output,
  tracker/host API responses.
- **Prefer editing existing files.** Don't create new modules or
  abstractions unless the task requires it.
- **Reversible vs risky actions.** Tests, formatting, typecheck runs
  are free. Git operations (especially push, force-push, rebase) and
  tracker mutations (create/close/comment) need user authorization in
  the absence of explicit instructions like "ship it."
- **Commit messages explain the why, not the what.** The diff shows
  what; the message explains the motivation.
