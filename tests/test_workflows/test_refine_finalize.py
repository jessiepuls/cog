"""Tests for RefineWorkflow finalize methods."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from cog.core.context import ExecutionContext
from cog.workflows.refine import (
    InterviewEnd,
    InterviewTurn,
    RefineWorkflow,
    ReviewDecision,
    ReviewOutcome,
)
from tests.fakes import InMemoryStateCache, ScriptedRewriteRunner, make_item, make_stage_result


def _make_ctx(tmp_path: Path, *, item=None, telemetry=None) -> ExecutionContext:
    return ExecutionContext(
        project_dir=tmp_path,
        tmp_dir=tmp_path / "tmp",
        state_cache=InMemoryStateCache(),
        headless=False,
        item=item,
        telemetry=telemetry,
    )


def _sentinel_transcript() -> list[InterviewTurn]:
    return [
        InterviewTurn(
            assistant_message="Q?",
            user_message="A.",
            cost_usd=0.1,
            duration_seconds=1.0,
        ),
        InterviewTurn(
            assistant_message="Done.",
            user_message=None,
            cost_usd=0.1,
            duration_seconds=1.0,
            end=InterviewEnd.SENTINEL,
        ),
    ]


def _user_end_transcript() -> list[InterviewTurn]:
    return [
        InterviewTurn(
            assistant_message="Q?",
            user_message=None,
            cost_usd=0.1,
            duration_seconds=1.0,
            end=InterviewEnd.USER,
        )
    ]


def _make_wf_with_data(
    item_id: str,
    *,
    transcript: list[InterviewTurn],
    review: ReviewOutcome,
    tracker: AsyncMock,
) -> RefineWorkflow:
    wf = RefineWorkflow(runner=ScriptedRewriteRunner(""), tracker=tracker)
    wf._transcripts[item_id] = transcript
    wf._review_outcomes[item_id] = review
    return wf


# ---------------------------------------------------------------------------
# finalize_success
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_finalize_success_updates_body_via_tracker(tmp_path):
    tracker = AsyncMock()
    item = make_item(item_id="1", title="Old title", body="Old body")
    review = ReviewOutcome(
        decision=ReviewDecision.ACCEPT, final_body="New body", final_title="Old title"
    )
    wf = _make_wf_with_data("1", transcript=_sentinel_transcript(), review=review, tracker=tracker)
    telemetry = AsyncMock()
    ctx = _make_ctx(tmp_path, item=item, telemetry=telemetry)
    results = [make_stage_result("rewrite", cost=0.5)]
    await wf.finalize_success(ctx, results)
    tracker.update_body.assert_awaited_once()
    call_args = tracker.update_body.call_args
    assert call_args[0][1] == "New body"


@pytest.mark.asyncio
async def test_finalize_success_updates_title_when_proposed(tmp_path):
    tracker = AsyncMock()
    item = make_item(item_id="2", title="Old title", body="B")
    review = ReviewOutcome(
        decision=ReviewDecision.ACCEPT, final_body="New body", final_title="New title"
    )
    wf = _make_wf_with_data("2", transcript=_sentinel_transcript(), review=review, tracker=tracker)
    telemetry = AsyncMock()
    ctx = _make_ctx(tmp_path, item=item, telemetry=telemetry)
    await wf.finalize_success(ctx, [make_stage_result("rewrite")])
    call_kwargs = tracker.update_body.call_args[1]
    assert call_kwargs.get("title") == "New title"


@pytest.mark.asyncio
async def test_finalize_success_leaves_title_unchanged_when_same(tmp_path):
    tracker = AsyncMock()
    item = make_item(item_id="3", title="Same title", body="B")
    review = ReviewOutcome(
        decision=ReviewDecision.ACCEPT, final_body="New body", final_title="Same title"
    )
    wf = _make_wf_with_data("3", transcript=_sentinel_transcript(), review=review, tracker=tracker)
    telemetry = AsyncMock()
    ctx = _make_ctx(tmp_path, item=item, telemetry=telemetry)
    await wf.finalize_success(ctx, [make_stage_result("rewrite")])
    call_kwargs = tracker.update_body.call_args[1]
    assert call_kwargs.get("title") is None


@pytest.mark.asyncio
async def test_finalize_success_swaps_labels_needs_refinement_to_agent_ready(tmp_path):
    tracker = AsyncMock()
    item = make_item(item_id="4", title="T", body="B")
    review = ReviewOutcome(decision=ReviewDecision.ACCEPT, final_body="B2", final_title="T")
    wf = _make_wf_with_data("4", transcript=_sentinel_transcript(), review=review, tracker=tracker)
    telemetry = AsyncMock()
    ctx = _make_ctx(tmp_path, item=item, telemetry=telemetry)
    await wf.finalize_success(ctx, [make_stage_result("rewrite")])
    tracker.remove_label.assert_awaited_once_with(item, "needs-refinement")
    tracker.add_label.assert_any_await(item, "agent-ready")


@pytest.mark.asyncio
async def test_finalize_success_applies_partially_refined_on_user_end(tmp_path):
    tracker = AsyncMock()
    item = make_item(item_id="5", title="T", body="B")
    review = ReviewOutcome(decision=ReviewDecision.ACCEPT, final_body="B2", final_title="T")
    wf = _make_wf_with_data("5", transcript=_user_end_transcript(), review=review, tracker=tracker)
    telemetry = AsyncMock()
    ctx = _make_ctx(tmp_path, item=item, telemetry=telemetry)
    await wf.finalize_success(ctx, [make_stage_result("rewrite")])
    tracker.ensure_label.assert_awaited_once()
    tracker.add_label.assert_any_await(item, "partially-refined")


@pytest.mark.asyncio
async def test_finalize_success_omits_partially_refined_on_sentinel_end(tmp_path):
    tracker = AsyncMock()
    item = make_item(item_id="6", title="T", body="B")
    review = ReviewOutcome(decision=ReviewDecision.ACCEPT, final_body="B2", final_title="T")
    wf = _make_wf_with_data("6", transcript=_sentinel_transcript(), review=review, tracker=tracker)
    telemetry = AsyncMock()
    ctx = _make_ctx(tmp_path, item=item, telemetry=telemetry)
    await wf.finalize_success(ctx, [make_stage_result("rewrite")])
    tracker.ensure_label.assert_not_awaited()
    add_calls = [str(c) for c in tracker.add_label.call_args_list]
    assert not any("partially-refined" in c for c in add_calls)


@pytest.mark.asyncio
async def test_finalize_success_writes_report_to_reports_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    tracker = AsyncMock()
    item = make_item(item_id="7", title="My Issue", body="B")
    review = ReviewOutcome(
        decision=ReviewDecision.ACCEPT, final_body="New body", final_title="My Issue"
    )
    wf = _make_wf_with_data("7", transcript=_sentinel_transcript(), review=review, tracker=tracker)
    telemetry = AsyncMock()
    ctx = _make_ctx(tmp_path, item=item, telemetry=telemetry)
    await wf.finalize_success(ctx, [make_stage_result("rewrite")])
    from cog.state_paths import project_state_dir

    reports_dir = project_state_dir(tmp_path) / "reports"
    report_files = list(reports_dir.glob("*-refine-*.md"))
    assert len(report_files) == 1
    content = report_files[0].read_text()
    assert "My Issue" in content


@pytest.mark.asyncio
async def test_finalize_success_writes_telemetry_with_interview_plus_rewrite_stages(tmp_path):
    tracker = AsyncMock()
    item = make_item(item_id="8", title="T", body="B")
    review = ReviewOutcome(decision=ReviewDecision.ACCEPT, final_body="B2", final_title="T")
    wf = _make_wf_with_data("8", transcript=_sentinel_transcript(), review=review, tracker=tracker)
    telemetry = AsyncMock()
    ctx = _make_ctx(tmp_path, item=item, telemetry=telemetry)
    await wf.finalize_success(ctx, [make_stage_result("rewrite", cost=0.5)])
    telemetry.write.assert_awaited_once()
    record = telemetry.write.call_args[0][0]
    stage_names = {s.stage for s in record.stages}
    assert "interview" in stage_names
    assert "rewrite" in stage_names


# ---------------------------------------------------------------------------
# finalize_noop
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_finalize_noop_comments_on_tracker_explaining_abandon(tmp_path):
    tracker = AsyncMock()
    item = make_item(item_id="9", title="T", body="B")
    review = ReviewOutcome(decision=ReviewDecision.ABANDON, final_body="B", final_title="T")
    wf = _make_wf_with_data("9", transcript=_sentinel_transcript(), review=review, tracker=tracker)
    ctx = _make_ctx(tmp_path, item=item)
    await wf.finalize_noop(ctx, [])
    tracker.comment.assert_awaited_once()
    comment_body = tracker.comment.call_args[0][1]
    assert "not apply" in comment_body or "chose not" in comment_body


@pytest.mark.asyncio
async def test_finalize_noop_preserves_needs_refinement_label(tmp_path):
    tracker = AsyncMock()
    item = make_item(item_id="10", title="T", body="B")
    review = ReviewOutcome(decision=ReviewDecision.ABANDON, final_body="B", final_title="T")
    wf = _make_wf_with_data("10", transcript=_sentinel_transcript(), review=review, tracker=tracker)
    ctx = _make_ctx(tmp_path, item=item)
    await wf.finalize_noop(ctx, [])
    tracker.remove_label.assert_not_awaited()


@pytest.mark.asyncio
async def test_finalize_noop_writes_report_with_body_after_placeholder(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    tracker = AsyncMock()
    item = make_item(item_id="11", title="T", body="Original")
    review = ReviewOutcome(decision=ReviewDecision.ABANDON, final_body="Proposed", final_title="T")
    wf = _make_wf_with_data("11", transcript=_sentinel_transcript(), review=review, tracker=tracker)
    ctx = _make_ctx(tmp_path, item=item)
    await wf.finalize_noop(ctx, [make_stage_result("rewrite")])
    from cog.state_paths import project_state_dir

    reports_dir = project_state_dir(tmp_path) / "reports"
    report_files = list(reports_dir.glob("*-refine-*.md"))
    assert len(report_files) == 1
    content = report_files[0].read_text()
    assert "Abandoned" in content
    assert "body unchanged" in content


@pytest.mark.parametrize(
    "outcome",
    [ReviewDecision.ACCEPT, ReviewDecision.ABANDON],
)
@pytest.mark.asyncio
async def test_finalize_deletes_transcript_file(tmp_path, monkeypatch, outcome):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    tracker = AsyncMock()
    item_id = "12"
    item = make_item(item_id=item_id, title="T", body="B")
    review = ReviewOutcome(decision=outcome, final_body="B", final_title="T")
    wf = _make_wf_with_data(
        item_id, transcript=_sentinel_transcript(), review=review, tracker=tracker
    )
    transcript_path = tmp_path / ".cog" / f"interview-{item_id}.md"
    transcript_path.parent.mkdir(parents=True, exist_ok=True)
    transcript_path.write_text("stub", encoding="utf-8")
    telemetry = AsyncMock() if outcome == ReviewDecision.ACCEPT else None
    ctx = _make_ctx(tmp_path, item=item, telemetry=telemetry)
    if outcome == ReviewDecision.ACCEPT:
        await wf.finalize_success(ctx, [make_stage_result("rewrite")])
    else:
        await wf.finalize_noop(ctx, [make_stage_result("rewrite")])
    assert not transcript_path.exists()
