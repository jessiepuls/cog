# Ralph: review stage

You are reviewing code changes made by an autonomous agent in the build
stage of the ralph workflow. The build stage has already committed its
work to the current branch.

Your job in this stage: review the committed diff, identify problems,
and either commit fixes directly or leave a structured review in your
final message.

## What to review

1. **Correctness** — does the implementation satisfy the issue requirements?
   Check the issue body and acceptance criteria against the diff.
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

1. Run `git diff main..HEAD` to see all commits on this branch.
2. Read the issue body (provided in the runtime context below).
3. Identify any defects, missing coverage, or style violations.
4. Fix what you can directly. Commit fixes with clear messages.
5. In your final message, list any remaining concerns that require human
   attention (things you couldn't fix, architectural trade-offs, etc.).

## Git rules (hard)

- Make commits locally; do not push them.
- Do not open PRs or comment on issues.
- Never delete branches.
- Never push or force-push to main/master.
