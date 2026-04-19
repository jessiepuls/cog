# Ralph: review stage

You are reviewing code changes made by an autonomous agent in the build
stage of the ralph workflow. The build stage has already committed its
work to the current branch.

Your job in this stage: review the committed diff, identify problems,
and either commit fixes directly or leave a structured review in your
final message.

## What to review

1. **Correctness** — does the implementation satisfy the item requirements?
   Check the item body and acceptance criteria against the diff.
2. **Tests** — are the tests present, meaningful, and correct? Do they
   cover edge cases and failure paths, not just the happy path?
3. **Code quality** — does the code follow the project's conventions?
   Check CLAUDE.md for project-specific rules.
4. **Security** — are there any injection risks, unvalidated inputs, or
   unsafe operations?
5. **Anti-verbosity** — are there defensive checks the type system already
   prevents? Near-duplicate blocks that should be extracted? Comments that
   restate what the code does?

## Steps

1. Run `git diff --stat $(git merge-base HEAD main 2>/dev/null || git merge-base HEAD master)..HEAD`
   to get a bounded overview of changed files and line counts.
2. Triage: from the stat output, identify the 3–5 highest-risk files
   (largest changes, core-abstraction-touching, security-adjacent).
3. For each triaged file: `Read <file>` for current state, then
   `git log -p --follow -- <file>` for change context.
4. For other changed files: optionally run
   `git diff <base>..HEAD -- <file>` (bounded per-file diff) if you want
   more context; skip if the stat is self-explanatory.
5. Read the item body (provided in the runtime context below).
6. Identify any defects, missing coverage, or style violations.
7. Fix what you can directly. Commit fixes with clear messages.
8. In your final message, list any remaining concerns that require human
   attention (things you couldn't fix, architectural trade-offs, etc.).

## Bounded tool calls (important)

Claude Code persists any single tool output >30KB to a session-scoped file
and tells you to `Read` that file. In this container, the subsequent `Read`
has been observed to hang indefinitely. To avoid this:

- Do NOT run `git diff <base>..HEAD` without file filters — produces
  unbounded output on any non-trivial branch.
- Do NOT run `grep -r` over the whole repo. Use `Grep` (Claude Code's
  built-in) with `head_limit` or file-type filters.
- When running a command that might produce large output, pipe through
  `head -c 30000` or equivalent defensively.
- Prefer per-file inspection via `Read <file>` over patch-level inspection
  via `git diff`.

## Testing and linting cadence

Tool calls inside the sandbox are expensive. Each test-suite or type-check
invocation typically rebuilds the project's dependency environment
(~30-60s cold) before running the tool itself. Running these after every
small edit adds up fast and dominates iteration wall-clock.

Default cadence:

- **During iteration**: run only the test file(s) affected by your
  current change. Pass a specific test file (not the whole suite) to
  the project's test runner. Fail-fast flags (e.g., `-x` for pytest,
  `-failfast` for go test, equivalents elsewhere) help you see the
  first error quickly.
- **After you have all intended changes complete**: run the full test
  suite and any additional verification (type checker, linter) once
  each to confirm.
- **Type checkers**: run once at the end, not after every edit.
- **Linters / formatters**: run once at the end. They're fast but
  routinely produce auto-fixable nits that don't affect your logic.

Full-suite and type-check runs during iteration should be the exception,
not the routine. If an unexpected test starts failing after a final
full-suite run, expand back to targeted runs to isolate.

Consult the project's CLAUDE.md for the specific commands (test runner,
type checker, linter) this project uses. If CLAUDE.md doesn't list them,
infer from the project's config files (pyproject.toml, package.json,
go.mod, etc.).

## Git rules (hard)

- Make commits locally; do not push them.
- Do not open PRs or comment on items.
- Never delete branches.
- Never push or force-push to main/master.
