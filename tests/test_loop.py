"""Tests for cog.loop primitives."""

from pathlib import Path

from cog.core.context import ExecutionContext
from cog.loop import LoopState, fresh_iteration_context
from tests.fakes import InMemoryStateCache


def _make_ctx(tmp_path: Path) -> ExecutionContext:
    return ExecutionContext(
        project_dir=tmp_path,
        tmp_dir=tmp_path / "tmp",
        state_cache=InMemoryStateCache(),
        headless=True,
    )


def test_loop_state_iteration_starts_at_zero() -> None:
    state = LoopState()
    assert state.iteration == 0


def test_loop_state_cumulative_cost_accumulates() -> None:
    state = LoopState()
    state.cumulative_cost_usd += 0.5
    state.cumulative_cost_usd += 0.3
    assert state.cumulative_cost_usd == 0.8


def test_fresh_iteration_context_resets_item_and_branch(tmp_path: Path) -> None:
    from tests.fakes import make_item

    ctx = _make_ctx(tmp_path)
    ctx.item = make_item()
    ctx.work_branch = "feat/123"
    fresh = fresh_iteration_context(ctx)
    assert fresh.item is None
    assert fresh.work_branch is None


def test_fresh_iteration_context_preserves_state_cache_and_telemetry(tmp_path: Path) -> None:
    cache = InMemoryStateCache()
    ctx = ExecutionContext(
        project_dir=tmp_path,
        tmp_dir=tmp_path / "tmp",
        state_cache=cache,
        headless=True,
    )
    fresh = fresh_iteration_context(ctx)
    assert fresh.state_cache is cache
    assert fresh.headless is True
    assert fresh.project_dir == tmp_path


def test_fresh_iteration_context_creates_new_tmp_dir(tmp_path: Path) -> None:
    ctx = _make_ctx(tmp_path)
    original_tmp = ctx.tmp_dir
    fresh = fresh_iteration_context(ctx)
    assert fresh.tmp_dir != original_tmp
    assert fresh.tmp_dir.exists()


def test_fresh_iteration_context_preserve_item_keeps_item(tmp_path: Path) -> None:
    # Regression: main-menu flow + `cog --item N` pre-populate base_ctx.item.
    # On iteration 1 we must preserve it, else select_item re-runs and (for
    # refine) errors because no ItemPicker is wired.
    from tests.fakes import make_item

    ctx = _make_ctx(tmp_path)
    item = make_item()
    ctx.item = item
    ctx.work_branch = "feat/123"
    fresh = fresh_iteration_context(ctx, preserve_item=True)
    assert fresh.item is item
    assert fresh.work_branch is None  # still reset
