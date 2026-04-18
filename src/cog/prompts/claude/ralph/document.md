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

## Steps

1. Run `git diff main..HEAD` to see what changed.
2. Read the issue body (provided in the runtime context below) for context
   on the intent of the change.
3. Update relevant documentation files (README, CHANGELOG, docstrings,
   inline comments that explain WHY — not what).
4. If there is nothing to document (internal refactors, test-only changes),
   say so in your final message and exit without committing.
5. Commit any documentation changes with a clear message.

## Git rules (hard)

- Make commits locally; do not push them.
- Do not open PRs or comment on issues.
- Never delete branches.
- Never push or force-push to main/master.
