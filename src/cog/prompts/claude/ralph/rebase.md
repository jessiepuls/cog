# Ralph: rebase before push

The work branch has completed its stages. Before pushing, rebase it onto
`origin/<default>` so the PR opens without merge-conflict noise.

## Steps

1. Run `git fetch origin` to make sure you have the latest.
2. Run `git rebase origin/<default>` (replace `<default>` with the actual
   default branch name, e.g. `origin/main`).
3. If the rebase is clean: done. Exit normally.
4. If conflicts: inspect the unmerged files, understand both sides,
   produce a meaningful merged version, `git add`, `git rebase --continue`.
   Repeat for each conflicting commit until the rebase completes.
5. If you cannot semantically resolve a conflict (e.g., both sides made
   genuinely incompatible decisions that need a human to arbitrate):
   run `git rebase --abort` and exit WITHOUT completing. Explain in your
   final message what you saw and why you could not resolve it.

The wrapper will `git rebase --abort` as a safety net if you leave the
rebase mid-state. No prose / commit expectations from you on the
clean-rebase path — you are just driving `git`.

## Bounded tool calls (important)

Claude Code persists any single tool output >30KB to a session-scoped file
and tells you to `Read` that file. In this container, the subsequent `Read`
has been observed to hang indefinitely. To avoid this:

- Do NOT run `git diff` without bounds on large repos.
- Do NOT run `grep -r` over the whole repo.
- When running a command that might produce large output, pipe through
  `head -c 30000` or equivalent defensively.
- Use `Read <file>` to inspect specific conflict files rather than broad
  shell expansions.
