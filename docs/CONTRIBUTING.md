# Contributing

## Dev setup

```bash
git clone https://github.com/jessiepuls/cog.git
cd cog
uv sync
uv run cog --help
```

Requires Python 3.12+ (managed automatically by uv). Build tool:
hatchling.

## Test / lint / type

```bash
uv run pytest                             # test suite (~25s, 1000+ tests)
uv run mypy src                           # type check
uv run ruff check .                       # lint
uv run ruff format --check .              # format check
uv run ruff format .                      # apply formatting
```

`pytest-asyncio` runs in auto mode. Warnings are treated as errors
(`filterwarnings = ["error"]`), so new warnings will fail the suite.

Sandbox integration tests are gated on `COG_INTEGRATION_TESTS=1` (they
require Docker).

## Testing conventions

- Most tests are async; pytest-asyncio auto mode is on.
- UI tests use Textual's `pilot.run_test(headless=True)`.
- Real subprocesses are faked via `FakeSubprocessRegistry` (see
  `tests/conftest.py` and `tests/test_trackers/conftest.py`). Don't
  call `gh` / `git` / `docker` directly from tests.
- For stage-executor tests, use `ctx_factory` and `echo_runner`
  fixtures from `tests/conftest.py` + `tests/fakes.py`.
- When replacing `push_screen_wait` in UI tests, note that the fake
  bypasses Textual's worker-context check. Regression tests for "must
  run in a worker" need to assert dispatch (e.g. `run_worker` was
  called), not just the downstream flow.

### Test granularity

- **Parametrize** when inputs vary but the behavior being checked is
  the same. Keep separate tests when the *intent* differs — each test
  should have a name that explains one thing.
- **Group assertions** within a single test when they verify the same
  outcome (e.g., all fields of a returned object). Split into separate
  tests when each assertion tests a distinct, independently-breakable
  behavior.
- **Pick one abstraction level per behavior.** A unit test and an
  integration test asserting the exact same thing is redundancy, not
  coverage.

## Code style

- **Tracker-agnostic language** outside `trackers/` and `hosts/`. Prefer
  "item" over "issue", "tracker" over "GitHub". PRs are host-scoped so
  "PR" is fine.
- **No comments that restate code.** Only comment WHY when non-obvious
  (a subtle invariant, a workaround, a reference to an incident).
- **Error handling at boundaries only.** Don't defensively validate
  inputs from internal code. Validate at: user input, subprocess output,
  tracker / host API responses.
- **Prefer editing existing files** over creating new modules or
  abstractions. Three similar lines is better than a premature
  abstraction.

## Reversible vs risky actions

- Tests, formatting, type-check runs are free.
- Git operations (especially `push`, `--force`, `reset --hard`,
  `rebase`) and tracker mutations (create / close / comment / label)
  need authorization in the absence of a durable instruction.
- Never skip hooks (`--no-verify`) or bypass signing unless explicitly
  asked.

## Commit messages

- Explain the **why**, not the what. The diff shows what.
- One commit per logical change. Don't bundle unrelated fixes.
- Co-author attribution is fine and expected for AI-pair commits.

## Prompts

Prompts live as markdown in `src/cog/prompts/claude/{ralph,refine}/*.md`
and load via `importlib.resources` at runtime. Each stage has its own
file. When changing prompt behavior, change the markdown — not Python
strings.

### Prompt-writing conventions

**Prefer on-demand fetching over context injection.** For any content
that is large (>few KB), variable, or only partially consumed: give
claude a pointer + instruction, not the content itself.

```
Bad:
### Issue body
{{full item body interpolated here — 20KB}}

Good:
To see the item body, run `gh issue view {id} --json body,comments`.
Fetch when you need it; don't assume you need the full body for every decision.
```

Benefits: smaller prompts start faster, claude-code's context
compaction can drop unused content, more headroom before stall classes
emerge, picks up live state on retry.

**Bounded tool calls warning.** All ralph prompts warn claude about the
>30KB tool-output persistence behavior. Preserve that warning in any
new prompt or when updating existing ones.

**Structured final-message sections.** Build prompts tell claude to end
with `### Summary / ### Key changes / ### Test plan`. The wrapper
extracts these by name to populate the PR body; don't change the
section names without updating the extraction.

## CI

Single workflow (`.github/workflows/ci.yml`): runs `pytest`, `mypy`,
and `ruff check`/`ruff format --check`. PRs must pass all four.

Ralph's `agent-failed` label is added by cog itself on CI failure
paths — it's separate from GitHub Actions state.
