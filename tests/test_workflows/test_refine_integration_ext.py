"""Extended integration tests for RefineWorkflow with rewrite + review."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from cog.core.context import ExecutionContext
from cog.core.tracker import IssueTracker
from cog.core.workflow import StageExecutor
from cog.workflows.refine import (
    InterviewEnd,
    InterviewTurn,
    RefineWorkflow,
    ReviewDecision,
    ReviewOutcome,
)
from tests.fakes import (
    InMemoryStateCache,
    RecordingEventSink,
    ScriptedInputProvider,
    ScriptedInterviewRunner,
    ScriptedRewriteRunner,
    make_needs_refinement_items,
)

_SENTINEL = "<<interview-complete>>"
_REWRITE_MSG = "### Title\nNew Title\n\n### Body\nNew body content."


def _make_wf(
    *,
    interview_responses: list[tuple[str, float]],
    rewrite_response: str = _REWRITE_MSG,
    review_outcome: ReviewOutcome,
    tracker: AsyncMock,
) -> RefineWorkflow:
    """Build a RefineWorkflow with all IO scripted."""

    class _ComboRunner:
        """Alternates interview turns then returns rewrite response."""

        def __init__(self):
            self._interview = ScriptedInterviewRunner(interview_responses)
            self._rewrite = ScriptedRewriteRunner(rewrite_response)
            self._call_count = 0
            self._total_interview_calls = len(interview_responses)

        async def stream(self, prompt, *, model):
            # First N calls are interview turns; next is rewrite
            if self._call_count < self._total_interview_calls:
                runner = self._interview
            else:
                runner = self._rewrite
            self._call_count += 1
            async for event in runner.stream(prompt, model=model):
                yield event

    wf = RefineWorkflow(runner=_ComboRunner(), tracker=tracker)

    async def _mocked_post_stages(ctx, results):
        assert ctx.item is not None
        wf._review_outcomes[ctx.item.item_id] = review_outcome

    wf.post_stages = _mocked_post_stages  # type: ignore[method-assign]
    return wf


@pytest.mark.asyncio
async def test_refine_iteration_sentinel_accept_updates_body_and_swaps_labels(tmp_path):
    tracker = AsyncMock(spec=IssueTracker)
    items = make_needs_refinement_items([42])
    tracker.list_by_label.return_value = items
    telemetry = AsyncMock()

    review = ReviewOutcome(
        decision=ReviewDecision.ACCEPT,
        final_body="New body content.",
        final_title="New Title",
    )
    wf = _make_wf(
        interview_responses=[(f"All done. {_SENTINEL}", 0.02)],
        review_outcome=review,
        tracker=tracker,
    )

    sink = RecordingEventSink()
    provider = ScriptedInputProvider([])
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
        telemetry=telemetry,
    )

    await StageExecutor().run(wf, ctx)

    tracker.update_body.assert_awaited_once()
    tracker.remove_label.assert_awaited_once_with(items[0], "needs-refinement")
    tracker.add_label.assert_any_await(items[0], "agent-ready")


@pytest.mark.asyncio
async def test_refine_iteration_user_early_end_accept_applies_partially_refined(tmp_path):
    tracker = AsyncMock(spec=IssueTracker)
    items = make_needs_refinement_items([43])
    tracker.list_by_label.return_value = items
    telemetry = AsyncMock()

    review = ReviewOutcome(
        decision=ReviewDecision.ACCEPT,
        final_body="Partial body.",
        final_title="T",
    )
    wf = _make_wf(
        interview_responses=[("First Q?", 0.01)],
        review_outcome=review,
        tracker=tracker,
    )

    # Simulate user end: inject transcript with USER end directly
    wf._transcripts[items[0].item_id] = [
        InterviewTurn(
            assistant_message="Q?",
            user_message=None,
            cost_usd=0.1,
            duration_seconds=1.0,
            end=InterviewEnd.USER,
        )
    ]

    sink = RecordingEventSink()
    provider = ScriptedInputProvider([None])  # user ends immediately
    ctx = ExecutionContext(
        project_dir=tmp_path,
        tmp_dir=tmp_path / "tmp",
        state_cache=InMemoryStateCache(),
        headless=False,
        item=items[0],
        event_sink=sink,
        input_provider=provider,
        telemetry=telemetry,
    )

    # Run finalize_success directly since we pre-wired transcript
    wf._review_outcomes[items[0].item_id] = review
    await wf.finalize_success(ctx, [])

    tracker.ensure_label.assert_awaited_once()
    tracker.add_label.assert_any_await(items[0], "partially-refined")


@pytest.mark.asyncio
async def test_refine_iteration_abandon_keeps_needs_refinement_and_posts_comment(tmp_path):
    tracker = AsyncMock(spec=IssueTracker)
    items = make_needs_refinement_items([44])

    review = ReviewOutcome(
        decision=ReviewDecision.ABANDON,
        final_body="body",
        final_title="T",
    )
    wf = RefineWorkflow(runner=AsyncMock(), tracker=tracker)
    wf._transcripts[items[0].item_id] = [
        InterviewTurn(
            assistant_message="Q?",
            user_message=None,
            cost_usd=0.1,
            duration_seconds=1.0,
            end=InterviewEnd.SENTINEL,
        )
    ]
    wf._review_outcomes[items[0].item_id] = review

    ctx = ExecutionContext(
        project_dir=tmp_path,
        tmp_dir=tmp_path / "tmp",
        state_cache=InMemoryStateCache(),
        headless=False,
        item=items[0],
    )

    await wf.finalize_noop(ctx, [])
    tracker.comment.assert_awaited_once()
    tracker.remove_label.assert_not_awaited()
