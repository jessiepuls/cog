# Refine: issue rewrite

You have just interviewed the user about a tracked item that needs
refinement. Your job is to translate that interview into a rewritten
item body so another agent (or a human) can implement it without
re-excavating the context.

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
Full rewritten item body in markdown. Suggested structure:

- **Problem / Motivation** — 1-3 paragraphs explaining why this item
  exists and what it solves.
- **Scope** — concrete list of what's included, with subsections if
  multiple concerns.
- **Non-goals / Out of scope** — what's explicitly excluded (often
  linking to follow-up items).
- **Open questions** — anything the interview did not fully resolve
  (especially important if the interview ended early).
- **Related** — links to sibling / blocker / dependency items.

## Style

- Don't ask questions in the output — this is the final body, not a
  conversation.
- Preserve the user's decisions verbatim when they were specific.
- Don't embellish with speculation not in the transcript.

## Early-end handling

If the "Refinement status" block in the input flags an early-end,
prepend this line at the very top of the Body section:

> ⚠ Refinement interview ended early; body is best-effort from a partial
> transcript. See "Open questions" for unresolved decisions.

Then populate "Open questions" with decisions that remained open.
