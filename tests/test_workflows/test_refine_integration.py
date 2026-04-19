"""Minimal integration test: select_item wire-up through StageExecutor pre-#18."""

from unittest.mock import AsyncMock

import pytest

from cog.core.context import ExecutionContext
from cog.core.tracker import IssueTracker
from cog.core.workflow import StageExecutor
from cog.workflows.refine import RefineWorkflow
from tests.fakes import FakeItemPicker, InMemoryStateCache, make_needs_refinement_items


async def test_refine_select_then_executor_exits_cleanly_when_stages_not_implemented(tmp_path):
    items = make_needs_refinement_items([7])
    tracker = AsyncMock(spec=IssueTracker)
    tracker.list_by_label.return_value = items

    ctx = ExecutionContext(
        project_dir=tmp_path,
        tmp_dir=tmp_path / "tmp",
        state_cache=InMemoryStateCache(),
        headless=False,
        item_picker=FakeItemPicker(return_value=None),
    )

    wf = RefineWorkflow(runner=AsyncMock(), tracker=tracker)

    # With a single item, select_item auto-selects, then stages() raises NotImplementedError
    # StageExecutor wraps that in StageError via finalize_error
    with pytest.raises(NotImplementedError):
        await StageExecutor().run(wf, ctx)

    assert ctx.item == items[0]
