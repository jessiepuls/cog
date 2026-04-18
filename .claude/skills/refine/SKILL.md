---
name: refine
description: Pick a needs-refinement issue assigned to me and refine it via grill-me, then rewrite the issue and swap labels to agent-ready. Use when the user says "refine an issue", "/refine", or wants to work through the refinement queue.
---

Pick an issue assigned to the current user that is labeled `needs-refinement` and refine it for implementation.

1. If the user passed an issue number as an argument, use that. Otherwise run `gh issue list --assignee @me --label needs-refinement` and:
   - If exactly one matches, use it.
   - If multiple, use the `AskUserQuestion` tool to present them as a selectable menu. Label each option `#<num> — <title>` (truncate long titles to fit). The tool caps at 4 options, so show the 4 oldest; the user can always pick "Other" and enter a different issue number.
   - If none, tell the user and stop.

2. Read the issue body and any directly linked issues/PRs for context. Explore relevant code paths before asking questions.

3. Invoke the `grill-me` skill via the Skill tool, passing the issue number, title, and body as args so the interview is grounded in the issue context.

4. Do not police the original issue description as a scope boundary. If adjacent work surfaces during the interview and it makes sense to do together (same file, same mental model, same PR review surface), just fold it in — do not flag it as "scope creep." The original description is a starting point, not a contract.

   The only time to propose splitting is when the resolved plan has become genuinely too large to ship as a single coherent change — e.g., it spans multiple independent subsystems, each with its own testing surface, such that a reviewer couldn't hold it all at once. In that case, propose a split into sibling issues and ask the user which pieces stay here. Prefer doing the work together when in doubt.

5. If a separate follow-up issue surfaces during the interview, it's OK to briefly sidetrack to capture it before returning to the primary refinement. The bar is "capture enough so a future session can start from this body without re-excavating context — not fully refined." Gather the problem, why it matters, any half-built state, a rough direction, related issues — then open it with `needs-refinement` so it enters the queue. Do not layer multiple sidetracks; one per refinement session. After opening, explicitly return to the primary interview.

6. **Design check.** If the change introduces or modifies UI, designs are part of refinement, not implementation. The implementing agent is headless and can't open Pencil. During this session:
   - Sketch the new component / state in `designs/meal-planning.pen` so it lines up with the rest of the screen and the spec is visually grounded.
   - Prompt the user to save the .pen file (Pencil does not save automatically).
   - After the user confirms saved, also review `designs/SCREENS.md` and update it if a new screen was added or removed, a screen was renamed, or key content node IDs inside a screen changed.
   - Commit the `.pen` (and SCREENS.md if changed) as a separate commit before rewriting the issue body.
   - Translate the visual decisions into the body in concrete prose — specific Lucide icon names, named CSS variables from the styleguide, layout structure, exact copy per state — so the agent never needs to consult the .pen file.

   If the change is purely backend / infra / non-UI, skip this step entirely.

7. **Help docs check.** Ask: would a user reading `app/frontend/src/lib/HelpPanel.svelte` learn about this feature/change after it ships? If the change adds, removes, or alters user-facing behavior described in HelpPanel, the refined issue must call out the specific HelpPanel section to update (and what to add/change/remove). If it's purely internal — refactor, perf, infra, dev tooling, bug fix with no UX-visible behavior change — skip. When in doubt, lean toward including the help-doc update; it's cheaper to write a sentence than to ship a feature users can't find.

8. When the interview concludes, rewrite the issue body to reflect the resolved plan: clear problem statement, explicit scope (with sub-sections if multiple concerns), non-goals, and related issues. Update the title if scope shifted. If a split was agreed, create the sibling issues via `gh issue create` (same assignee, `needs-refinement` label) and link them in Related.

9. Apply label changes: remove `needs-refinement`, add `agent-ready`. Keep the assignee unchanged.

10. Confirm completion to the user with the issue URL.

11. Check the remaining queue with `gh issue list --assignee @me --label needs-refinement`:
   - If none remain, tell the user the queue is empty and stop.
   - Otherwise, use `AskUserQuestion` to present the next selection menu directly (same 4-option format as step 1). If they pick one, restart from step 2 with that issue. If they pick "Other" and enter "no" / "stop" / similar, exit.
