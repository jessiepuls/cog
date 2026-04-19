### Summary

Adds `--max-iterations` to `cog ralph --loop` so unattended queue-drain
runs can be bounded. Implemented via a new `LoopState` counter in the
shared loop module; both headless and Textual call sites inherit.
Introduces a `fresh_iteration_context` helper so per-iteration state
(item, work_branch, tmp_dir) is reset cleanly between iterations while
cross-iteration state (cumulative cost, processed-this-loop set) is
preserved.

### Key changes

- `src/cog/loop.py` (new): `LoopState` + `fresh_iteration_context` primitives
- `src/cog/headless.py`: `run_headless` now loops until queue drained
- `src/cog/ui/screens/run.py`: `_run_loop` replaces `_run_once`
- `src/cog/cli.py`: new `--max-iterations` flag; implies `--loop`

### Test plan

- [ ] Run `cog ralph --loop --max-iterations 2` and verify it stops after 2 items
- [ ] Run without `--max-iterations` and verify the loop drains the queue fully
- [ ] Confirm cost accumulates correctly across iterations in the report
- [ ] Verify `--max-iterations 0` exits immediately without processing any items
- [ ] Check that a failed iteration does not count toward the max-iterations limit
