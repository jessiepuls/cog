# Ralph: document stage

You are updating documentation for code changes made in the build and
review stages of the ralph workflow. The branch already contains the
implementation commits.

Your job in this stage: update or create documentation that reflects
the new or changed behavior. This stage uses `tolerate_failure=True`,
so failures here do not abort the iteration — do your best, but don't
worry if some documentation is deferred.

## What to document

Focus on user-facing changes that aren't self-evident from the code:

1. **Public API changes** — new classes, functions, or parameters that
   callers need to know about.
2. **Configuration** — new env vars, config keys, or CLI flags.
3. **Behavior changes** — anything that affects how existing features work.
4. **Upgrade notes** — anything a user upgrading from a previous version
   needs to do.

## Item context

To see the item's current body and comments:

    gh issue view <item_id> --json body,comments --jq '.body,.comments'

where `<item_id>` is the number shown in the runtime context at the end
of this prompt. Fetch when you need it — not every decision requires the
full body. Claude Code persists any tool output >30KB to a session-scoped
file; pipe through `head -c 30000` or use `--jq` to stay bounded.

## Steps

1. Run `git diff --stat $(git merge-base HEAD main 2>/dev/null || git merge-base HEAD master)..HEAD`
   to get a scope overview of what changed.
2. For each changed user-facing file (README, CHANGELOG, docs/*, docstrings
   in public-API files): `Read` the file and check whether its content
   aligns with the changes.
3. Run `git log --oneline $(git merge-base HEAD main 2>/dev/null || git merge-base HEAD master)..HEAD`
   for bounded commit subjects to inform the narrative (NOT `-p`, which
   includes full patches).
4. Fetch the item body if needed (see **Item context** above) for context
   on the intent of the change.
5. Update relevant documentation files (README, CHANGELOG, docstrings,
   inline comments that explain WHY — not what).
6. If there is nothing to document (internal refactors, test-only changes),
   say so in your final message and exit without committing.
7. Commit any documentation changes with a clear message.

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

## Final message format

End your final message with four structured sections in this order:
`### Summary`, `### Key changes`, `### Test plan`, `### Follow-up items`.
The wrapper extracts each section into the PR body.

For Summary, Key changes, and Test plan: these describe what this PR
adds against the base branch, not what you did this iteration. Default
to copying each section forward verbatim from the prior stages' output.
Update a section only if there is inaccurate or missing information a
reviewer needs to understand the scope of changes to be merged.

If you made a judgment call between options where reviewer input would
help (timeouts, retry counts, naming, scoping decisions), call it out in
Summary so the reviewer can validate.

### Follow-up items

Optional. List only items you noticed during THIS stage. The wrapper
appends follow-up items from prior stages automatically — do NOT copy
them forward, or they will be duplicated in the PR body.

Use this section for anything the reviewer should know about that
doesn't fit Summary / Key changes / Test plan. Examples:

  - The test you wrote depends on a flaky external service
  - An unrelated bug you noticed in foo.py
  - A workaround you took because the proper fix would balloon the PR
  - Something worth filing as its own follow-up issue (e.g., "the
    `_compute_cost` divide-by-zero in telemetry.py deserves its own fix")

Skip this section entirely if there is nothing worth flagging.

## Git rules (hard)

- Make commits locally; do not push them.
- Do not open PRs or comment on items.
- Never delete branches.
- Never push or force-push to main/master.
