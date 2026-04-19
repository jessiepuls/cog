"""Tests for RalphWorkflow.select_item and _priority_tier."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

from cog.core.tracker import IssueTracker
from cog.workflows.ralph import RalphWorkflow, _priority_tier
from tests.fakes import InMemoryStateCache, make_item


def _make_workflow() -> RalphWorkflow:
    return RalphWorkflow(runner=AsyncMock(), tracker=AsyncMock(spec=IssueTracker))


def _make_ctx(cache: InMemoryStateCache | None = None):
    from pathlib import Path

    from cog.core.context import ExecutionContext

    return ExecutionContext(
        project_dir=Path("/tmp"),
        tmp_dir=Path("/tmp"),
        state_cache=cache or InMemoryStateCache(),
        headless=True,
    )


# --- _priority_tier ---


def test_priority_tier_no_pN_label_returns_999() -> None:
    item = make_item(labels=("agent-ready", "bug"))
    assert _priority_tier(item) == 999


def test_priority_tier_single_pN_returns_number() -> None:
    item = make_item(labels=("p2", "agent-ready"))
    assert _priority_tier(item) == 2


def test_priority_tier_multiple_pN_returns_minimum() -> None:
    item = make_item(labels=("p3", "p1", "p2"))
    assert _priority_tier(item) == 1


def test_priority_tier_ignores_malformed_labels() -> None:
    item = make_item(labels=("priority-1", "p1a", "pX", "agent-ready"))
    assert _priority_tier(item) == 999


def test_priority_tier_accepts_p0() -> None:
    item = make_item(labels=("p0",))
    assert _priority_tier(item) == 0


# --- select_item ---


async def test_select_item_returns_none_when_empty() -> None:
    wf = _make_workflow()
    wf._tracker.list_by_label = AsyncMock(return_value=[])
    ctx = _make_ctx()
    result = await wf.select_item(ctx)
    assert result is None


async def test_select_item_sorts_by_priority_tier_asc() -> None:
    t = datetime(2024, 1, 1, tzinfo=UTC)
    items = [
        make_item(item_id="3", labels=("p3",), created_at=t),
        make_item(item_id="1", labels=("p1",), created_at=t),
        make_item(item_id="2", labels=("p2",), created_at=t),
    ]
    wf = _make_workflow()
    wf._tracker.list_by_label = AsyncMock(return_value=items)
    wf._tracker.get = AsyncMock(side_effect=lambda iid: make_item(item_id=iid))
    ctx = _make_ctx()
    result = await wf.select_item(ctx)
    assert result is not None
    assert result.item_id == "1"


async def test_select_item_within_tier_sorts_by_created_at_asc() -> None:
    t1 = datetime(2024, 1, 1, tzinfo=UTC)
    t2 = datetime(2024, 1, 2, tzinfo=UTC)
    t3 = datetime(2024, 1, 3, tzinfo=UTC)
    items = [
        make_item(item_id="c", labels=("p1",), created_at=t3),
        make_item(item_id="a", labels=("p1",), created_at=t1),
        make_item(item_id="b", labels=("p1",), created_at=t2),
    ]
    wf = _make_workflow()
    wf._tracker.list_by_label = AsyncMock(return_value=items)
    wf._tracker.get = AsyncMock(side_effect=lambda iid: make_item(item_id=iid))
    ctx = _make_ctx()
    result = await wf.select_item(ctx)
    assert result is not None
    assert result.item_id == "a"


async def test_select_item_skips_processed_in_current_loop() -> None:
    t = datetime(2024, 1, 1, tzinfo=UTC)
    item = make_item(item_id="1", created_at=t)
    wf = _make_workflow()
    wf._tracker.list_by_label = AsyncMock(return_value=[item])
    wf._processed_this_loop.add((item.tracker_id, item.item_id))
    ctx = _make_ctx()
    result = await wf.select_item(ctx)
    assert result is None


async def test_select_item_skips_processed_in_state_cache() -> None:
    t = datetime(2024, 1, 1, tzinfo=UTC)
    item = make_item(item_id="1", created_at=t)
    cache = InMemoryStateCache()
    cache.mark_processed(item, "success")
    wf = _make_workflow()
    wf._tracker.list_by_label = AsyncMock(return_value=[item])
    ctx = _make_ctx(cache)
    result = await wf.select_item(ctx)
    assert result is None


async def test_select_item_adds_chosen_to_local_set() -> None:
    t = datetime(2024, 1, 1, tzinfo=UTC)
    item = make_item(item_id="42", created_at=t)
    wf = _make_workflow()
    wf._tracker.list_by_label = AsyncMock(return_value=[item])
    wf._tracker.get = AsyncMock(return_value=make_item(item_id="42"))
    ctx = _make_ctx()
    result = await wf.select_item(ctx)
    assert result is not None
    assert (item.tracker_id, item.item_id) in wf._processed_this_loop
