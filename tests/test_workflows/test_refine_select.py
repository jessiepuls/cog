"""Tests for RefineWorkflow.select_item (picker-independent logic)."""

from unittest.mock import AsyncMock

import pytest

from cog.core.errors import WorkflowError
from cog.core.tracker import IssueTracker
from cog.workflows.refine import RefineWorkflow
from tests.fakes import FakeItemPicker, InMemoryStateCache, make_needs_refinement_items


def _make_workflow(tracker):
    return RefineWorkflow(runner=AsyncMock(), tracker=tracker)


def _make_ctx(tmp_path, *, item_picker=None):
    from cog.core.context import ExecutionContext

    return ExecutionContext(
        project_dir=tmp_path,
        tmp_dir=tmp_path / "tmp",
        state_cache=InMemoryStateCache(),
        headless=False,
        item_picker=item_picker,
    )


async def test_select_item_returns_none_when_queue_empty(tmp_path):
    tracker = AsyncMock(spec=IssueTracker)
    tracker.list_by_label.return_value = []
    wf = _make_workflow(tracker)
    result = await wf.select_item(_make_ctx(tmp_path))
    assert result is None


async def test_select_item_auto_selects_when_single_item(tmp_path):
    items = make_needs_refinement_items([42])
    tracker = AsyncMock(spec=IssueTracker)
    tracker.list_by_label.return_value = items
    picker = FakeItemPicker(return_value=items[0])
    wf = _make_workflow(tracker)

    result = await wf.select_item(_make_ctx(tmp_path, item_picker=picker))

    assert result == items[0]
    assert picker.called_with == []  # picker NOT invoked


async def test_select_item_invokes_picker_when_multiple_items(tmp_path):
    items = make_needs_refinement_items([1, 2, 3])
    tracker = AsyncMock(spec=IssueTracker)
    tracker.list_by_label.return_value = items
    picker = FakeItemPicker(return_value=items[1])
    wf = _make_workflow(tracker)

    result = await wf.select_item(_make_ctx(tmp_path, item_picker=picker))

    assert result == items[1]
    assert len(picker.called_with) == 3


async def test_select_item_passes_items_sorted_by_created_at_asc_to_picker(tmp_path):
    # items created in reverse order so unsorted list would be [3,2,1]
    items = make_needs_refinement_items([3, 2, 1])
    tracker = AsyncMock(spec=IssueTracker)
    tracker.list_by_label.return_value = items
    picker = FakeItemPicker(return_value=None)
    wf = _make_workflow(tracker)

    await wf.select_item(_make_ctx(tmp_path, item_picker=picker))

    assert [i.item_id for i in picker.called_with] == ["3", "2", "1"]


async def test_select_item_returns_picker_result(tmp_path):
    items = make_needs_refinement_items([10, 20])
    tracker = AsyncMock(spec=IssueTracker)
    tracker.list_by_label.return_value = items
    picker = FakeItemPicker(return_value=items[1])
    wf = _make_workflow(tracker)

    result = await wf.select_item(_make_ctx(tmp_path, item_picker=picker))

    assert result == items[1]


async def test_select_item_returns_none_when_picker_cancels(tmp_path):
    items = make_needs_refinement_items([1, 2])
    tracker = AsyncMock(spec=IssueTracker)
    tracker.list_by_label.return_value = items
    picker = FakeItemPicker(return_value=None)
    wf = _make_workflow(tracker)

    result = await wf.select_item(_make_ctx(tmp_path, item_picker=picker))

    assert result is None


async def test_select_item_raises_when_picker_missing_and_multiple_items(tmp_path):
    items = make_needs_refinement_items([1, 2, 3])
    tracker = AsyncMock(spec=IssueTracker)
    tracker.list_by_label.return_value = items
    wf = _make_workflow(tracker)

    with pytest.raises(WorkflowError, match="ItemPicker"):
        await wf.select_item(_make_ctx(tmp_path, item_picker=None))
