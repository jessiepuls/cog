# Ralph: CI fix stage

The PR's CI failed. Your job this iteration:

1. **Reproduce the failure locally.** The failing checks are listed in the
   runtime context below with links. To see the CI output, run
   `gh run view <run-id> --log-failed` where `<run-id>` is the numeric ID
   from the check link (e.g. `.../actions/runs/12345/job/678` → run-id is
   `12345`). Also try the project's test / lint / type commands directly
   based on what the check name suggests.
2. **If you can reproduce**: fix the failure and commit. The wrapper
   pushes your commit and re-triggers CI.
3. **If you cannot reproduce** after reasonable effort (check only fires
   in CI env, ambiguous error, etc.): **exit WITHOUT committing.** In
   your final message, explain what you tried, what you believe the
   failure is, and what would unblock it. The wrapper posts that on the
   PR for a human to triage.

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

## Testing and linting cadence

Tool calls inside the sandbox are expensive. Run only the test file(s)
affected by your fix during iteration, then the full suite once at the end.

Consult the project's CLAUDE.md for the specific commands this project uses.

## Commit discipline

Commit exactly once when the fix passes the full suite. If you cannot
reproduce or fix the failure, exit without committing.

## Git rules (hard)

- Make commits locally; do not push them. The wrapper pushes the branch.
- Do not open PRs or comment on items.
- Never delete branches or push to the default branch.
