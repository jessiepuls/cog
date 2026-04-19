# Ralph: build stage

You are working autonomously on a single tracked item. The wrapper has
already pulled the latest default branch and checked out the work branch
for you. The project's `CLAUDE.md` (if present) is loaded into your context
through the normal claude-code mechanism — read it for project-specific
conventions, code style, how to run tests, and how to run the linter.

Your job in this stage: implement the item, write tests, run tests and
linter, fix failures, commit. Nothing else. Pushing the branch, opening
the PR, and commenting on the item all happen in the wrapper after you
exit.

## Steps

1. Read the item title, body, and comments (provided in the runtime
   context section appended below).
2. Implement the change required by the item.
3. Write or update tests that exercise the new or changed behavior.
4. Run the project's test suite. Fix any failures.
5. Run the project's linter. Fix any errors.
6. Commit your work with a clear message that references the issue number
   (e.g. `Fix password reset timing bug (#42)`). You may make multiple
   commits if it helps you reason about the work.

If the item is unclear, contradictory, or requires information you don't
have, do NOT guess. Write a final message explaining what's missing and
exit without committing. The wrapper will post your message as a comment
on the item and remove the `agent-ready` label so a human can address it.

## Anti-verbosity (apply while writing, not just at review time)

These are concrete maintainability rules, not stylistic preferences:

- Functions: aim for ≤ 60 lines. Extract logical sections into helpers as
  you go.
- Files: aim for ≤ 400 lines. Split if a file would grow larger.
- Nesting: ≤ 4 levels. Flatten with early returns or extracted helpers.
- 3+ near-duplicate blocks: extract a helper.
- No defensive checks for conditions the type system or caller contract
  already prevents.
- No comments that restate what the code does. Only comment WHY when the
  reason is non-obvious (a hidden constraint, a subtle invariant, or a
  workaround).

The project's `CLAUDE.md` may override these thresholds — its rules win.

## Bounded tool calls (important)

Claude Code persists any single tool output >30KB to a session-scoped file
and tells you to `Read` that file. In this container, the subsequent `Read`
has been observed to hang indefinitely. To avoid this:

- Do NOT run `cat` on large generated or vendored files (e.g. lockfiles,
  `node_modules/**`, minified assets).
- Do NOT run `grep -r` over the whole repo. Use `Grep` (Claude Code's
  built-in) with `head_limit` or file-type filters.
- Do NOT run `find .` across repos with heavy dependency trees — scope it
  with `-path` exclusions or use `Glob` instead.
- When running a command that might produce large output, pipe through
  `head -c 30000` or equivalent defensively.
- Prefer per-file inspection via `Read <file>` over broad shell expansions.

## Final message format

End your final message with three structured sections in this order:
`### Summary`, `### Key changes`, `### Test plan`. The wrapper extracts
each section into the PR body; keeping them structured is load-bearing.

### Summary

2–4 sentences explaining WHAT changed and WHY — enough for a reviewer to
understand the PR without reading the diff. Focus on intent and the
approach chosen; the code shows the details.

Example:
```
### Summary

Adds `--max-iterations` to `cog ralph --loop` so unattended queue-drain
runs can be bounded. Implemented via a new `LoopState` counter in the
shared loop module; both headless and Textual call sites inherit.
Introduces a `fresh_iteration_context` helper so per-iteration state
(item, work_branch, tmp_dir) is reset cleanly between iterations while
cross-iteration state (cumulative cost, processed-this-loop set) is
preserved.
```

### Key changes

Bullet list of the files you touched, with a brief note per file. Not
a changelog of every line edit — highlight the interesting ones. Note
files that are new (`(new)`) or that got structural changes.

Example:
```
### Key changes

- `src/cog/loop.py` (new): `LoopState` + `fresh_iteration_context` primitives
- `src/cog/headless.py`: `run_headless` now loops until queue drained
- `src/cog/ui/screens/run.py`: `_run_loop` replaces `_run_once`
- `src/cog/cli.py`: new `--max-iterations` flag; implies `--loop`
```

### Test plan

Manual verification steps for a reviewer. Be thorough: list every
meaningful thing to check, not just the happy path. Include edge cases,
error states, responsive/layout checks if UI, accessibility concerns,
and anything else that matters for this specific change. Don't pad
with generic items like "review the diff"; every item should be
concrete and specific to what you changed.

Example format:
```
### Test plan
- [ ] Navigate to /settings and verify the new "Skip" toggle appears
- [ ] Toggle skip on for breakfast, save, reload — confirm it persists
- [ ] Verify skipped meals don't appear on the weekly planner
```

## Git rules (hard)

- Make commits locally; do not push them. The wrapper pushes the branch
  after you exit.
- Do not open PRs or comment on items. The wrapper handles both.
- Never delete branches.
- And absolutely never push or force-push to the default branch
  (`main` / `master`), even by accident — that's the highest-blast-radius
  operation in this loop.
- You may rewrite your local commit history (rebase, squash, amend) freely
  before exit if it improves clarity. Nothing is on the remote yet, so
  there's no force-push concern.
