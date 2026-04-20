"""Integration tests for RefineWorkflow."""

from unittest.mock import AsyncMock

import pytest

from cog.core.context import ExecutionContext
from cog.core.tracker import IssueTracker
from cog.core.workflow import StageExecutor
from cog.workflows.refine import InterviewEnd, RefineWorkflow
from tests.fakes import (
    FakeItemPicker,
    InMemoryStateCache,
    RecordingEventSink,
    ScriptedInputProvider,
    ScriptedInterviewRunner,
    make_needs_refinement_items,
)

_SENTINEL = "<<interview-complete>>"


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

    # With a single item, select_item auto-selects, then pre_stages (interview)
    # has no event_sink/input_provider → raises AssertionError
    with pytest.raises((NotImplementedError, AssertionError)):
        await StageExecutor().run(wf, ctx)

    assert ctx.item == items[0]


@pytest.mark.asyncio
async def test_refine_interview_end_to_end_with_scripted_runner(tmp_path):
    """Full pre_stages run through ScriptedInterviewRunner + ScriptedInputProvider."""
    responses = [
        ("First question?", 0.01),
        (f"All set! {_SENTINEL}", 0.02),
    ]
    runner = ScriptedInterviewRunner(responses)
    tracker = AsyncMock(spec=IssueTracker)
    items = make_needs_refinement_items([42])
    tracker.list_by_label.return_value = items

    sink = RecordingEventSink()
    provider = ScriptedInputProvider(["my answer"])

    tmp_dir = tmp_path / "tmp"
    tmp_dir.mkdir()
    ctx = ExecutionContext(
        project_dir=tmp_path,
        tmp_dir=tmp_dir,
        state_cache=InMemoryStateCache(),
        headless=False,
        item=items[0],
        event_sink=sink,
        input_provider=provider,
    )

    wf = RefineWorkflow(runner=runner, tracker=tracker)
    await wf.pre_stages(ctx)

    transcript = wf._transcripts[items[0].item_id]
    assert len(transcript) == 2
    assert transcript[0].end == InterviewEnd.NOT_ENDED
    assert transcript[0].user_message == "my answer"
    assert transcript[1].end == InterviewEnd.SENTINEL
    assert _SENTINEL not in transcript[1].assistant_message
