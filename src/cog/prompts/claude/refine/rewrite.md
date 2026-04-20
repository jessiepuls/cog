# Refine: issue rewrite

You have just interviewed the user about a tracked item. Your job is
to crystallize the decisions made during that interview — plus the
context from the original item — into an implementation specification
detailed enough that a headless agent can ship this change without
asking any clarifying questions.

This is a spec, not a summary. If your output is shorter than the
original item body, you have compressed when you should have specified.

## Input
- Original item (title + body + comments)
- Full interview transcript
- Early-end flag (present only if the user ended the interview early)

## Output format

Your final message MUST include exactly two sections:

### Title
Single line. Propose an updated title OR repeat the original if it
still fits. Keep concise; titles >80 chars get truncated in many
tracker UIs.

### Body
A full rewritten item body in markdown. Use whatever structure the
problem demands — tables, file-by-file breakdowns, code fences, nested
subsections. Below is a *flexible scaffold*, not a template to fill.
Prefer more structure than less when it helps a reader pattern-match
the problem quickly.

Common sections:
- **Problem / Motivation** — what this exists to solve and why it
  matters. As long as the problem actually is.
- **Scope** — concrete list of what's included. For multi-surface
  items, one subsection per surface. Name specific files, functions,
  endpoints, schemas, screens — not generic categories.
- **Non-goals** — what's explicitly out of scope (often linking to
  follow-up items).
- **Open questions** — anything the interview did not fully resolve
  (especially important if the interview ended early).
- **Related** — links to sibling / blocker / dependency items.

## Concreteness bar

Every decision from the interview must land as a specific
implementation instruction in the body. "Handle errors gracefully" is
not a decision — *"on connection failure, log at warning and retry up
to 3× with 2^n backoff, then raise ConnectionError"* is.

- Name files and functions by path, not by role.
- Give data shapes (field names, types) when they matter.
- Enumerate error cases and edge cases rather than gesturing at them.
- State default values, thresholds, and limits numerically.

## Style

- Synthesize from the interview into specific, implementation-ready
  prose. You may extrapolate specifics from decisions the user made —
  e.g. if the user agreed to "use the existing cache layer," you may
  name the actual cache module if you can identify it.
- Do NOT invent decisions the user did not make. If something was not
  covered, put it under Open questions.
- Don't ask questions in the output — this is the final body, not a
  conversation.

## Early-end handling

If the input flags an early-end, prepend this line at the very top of
the Body section:

> ⚠ Refinement interview ended early; body is best-effort from a partial
> transcript. See "Open questions" for unresolved decisions.

Then populate "Open questions" with decisions that remained open.
