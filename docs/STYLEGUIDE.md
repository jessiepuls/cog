# UI / Textual conventions

Bug-discovered rules. Apply these when adding views or interactive widgets.

## Rendering

- **Escape `[` in dynamic text rendered through Textual markup.** `[name]`
  is parsed as a style tag and silently dropped; use `\[name]` (raw
  f-string) when rendering label-like strings into Static / Label /
  ListItem. Don't escape `]` — it's only special when paired with an
  opening `[`, so escaping renders a literal backslash. See `_row_text`
  in `src/cog/ui/widgets/issues_browser.py`.
- **Input shape.** `height: 1` + `border-bottom` leaves no room for
  text — the border eats the row. Use Textual's natural Input shape
  (`height: 3`, `border: tall`) unless you have a reason not to.

## Keybindings and focus

- **Don't put a child Input behind a parent BINDINGS that uses printable
  keys.** A `Binding("r", ...)` on the parent fires even when a child
  Input has focus, swallowing the keystroke. Either move the binding to
  a non-Input widget, or override `check_action` to return `False` for
  that action while the input has focus. See `IssuesView.check_action`.
- **Split-pane keys: `ctrl+,` narrows / `ctrl+.` widens, 5% per press,
  bounded `[20, 80]`.** Stored as `_split_pct: int`, applied via
  `widget.styles.width = f"{pct}%"`. See `refine.py` and `issues.py`
  `_apply_split`.

## Filter and list rebuild perf

- **`ListView.extend([items])`, not awaited per-item `append`.** A loop
  of awaited appends yields to the event loop per item, so N items = N
  render frames. `extend` does one `mount(*items)`. Always batch when
  re-rendering a ListView.
- **Debounce input-driven rebuilds (~120ms).** When every keystroke
  spawns an `exclusive=True` worker, fast typing cancels in-flight work
  and the filter feels laggy. Use `set_timer` cancelled+restarted in
  `on_input_changed`, with the actual filter+rebuild in the timer
  callback. See `IssuesView.on_input_changed`.

## Mutations from a view

- **After a tracker mutation from a view, update the local cache in
  place** (e.g. `dataclasses.replace`) and re-render rather than
  refetching. Update derived counters (open/closed totals) so the
  status row stays accurate. See `IssuesView._close_item`.
- **Confirm destructive actions with a ModalScreen** (`y` / `n` /
  `escape` bindings). Push via `app.push_screen(Modal, callback)`,
  dismiss with `True` / `False`. See `src/cog/ui/screens/quit_confirm.py`
  and `src/cog/ui/screens/close_confirm.py`.
