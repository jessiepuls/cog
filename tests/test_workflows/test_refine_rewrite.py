"""Tests for RefineWorkflow rewrite stage."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from cog.core.context import ExecutionContext
from cog.core.section_extractor import extract_sections
from cog.workflows.refine import InterviewEnd, InterviewTurn, RefineWorkflow
from tests.fakes import InMemoryStateCache, ScriptedRewriteRunner, make_item


def _make_ctx(tmp_path: Path, item=None) -> ExecutionContext:
    return ExecutionContext(
        project_dir=tmp_path,
        tmp_dir=tmp_path / "tmp",
        state_cache=InMemoryStateCache(),
        headless=False,
        item=item,
    )


def _make_sentinel_transcript() -> list[InterviewTurn]:
    return [
        InterviewTurn(
            assistant_message="What does this item need?",
            user_message="It needs caching.",
            cost_usd=0.1,
            duration_seconds=1.0,
        ),
        InterviewTurn(
            assistant_message="Got it.",
            user_message=None,
            cost_usd=0.1,
            duration_seconds=1.0,
            end=InterviewEnd.SENTINEL,
        ),
    ]


def _make_user_end_transcript() -> list[InterviewTurn]:
    return [
        InterviewTurn(
            assistant_message="What does this need?",
            user_message=None,
            cost_usd=0.1,
            duration_seconds=1.0,
            end=InterviewEnd.USER,
        ),
    ]


def _make_workflow(transcript: list[InterviewTurn]) -> RefineWorkflow:
    runner = ScriptedRewriteRunner("### Title\nNew title\n\n### Body\nNew body")
    tracker = AsyncMock()
    wf = RefineWorkflow(runner=runner, tracker=tracker)
    return wf


# ---------------------------------------------------------------------------
# Prompt loading
# ---------------------------------------------------------------------------


def test_load_rewrite_prompt_reads_package_data():
    from importlib.resources import files

    text = files("cog.prompts.claude.refine").joinpath("rewrite.md").read_text(encoding="utf-8")
    assert "### Title" in text
    assert "### Body" in text


# ---------------------------------------------------------------------------
# Prompt assembly
# ---------------------------------------------------------------------------


def test_rewrite_prompt_includes_original_body_and_comments(tmp_path):
    from datetime import UTC, datetime

    from cog.core.item import Comment

    comment = Comment(
        author="alice", body="Good point", created_at=datetime(2024, 1, 1, tzinfo=UTC)
    )
    item = make_item(item_id="1", title="My issue", body="Original body", comments=(comment,))
    wf = _make_workflow([])
    wf._transcripts["1"] = _make_sentinel_transcript()
    ctx = _make_ctx(tmp_path, item=item)
    prompt = wf._build_rewrite_prompt(ctx)
    assert "Original body" in prompt
    assert "alice" in prompt
    assert "Good point" in prompt


def test_rewrite_prompt_includes_full_transcript(tmp_path):
    item = make_item(item_id="2", title="T", body="B")
    wf = _make_workflow([])
    transcript = _make_sentinel_transcript()
    wf._transcripts["2"] = transcript
    ctx = _make_ctx(tmp_path, item=item)
    prompt = wf._build_rewrite_prompt(ctx)
    assert "What does this item need?" in prompt
    assert "It needs caching." in prompt
    assert "Got it." in prompt


def test_rewrite_prompt_omits_early_end_block_on_sentinel_end(tmp_path):
    item = make_item(item_id="3", title="T", body="B")
    wf = _make_workflow([])
    wf._transcripts["3"] = _make_sentinel_transcript()
    ctx = _make_ctx(tmp_path, item=item)
    prompt = wf._build_rewrite_prompt(ctx)
    # The runtime-injected block uses "## Refinement status" as a heading
    assert "## Refinement status" not in prompt


def test_rewrite_prompt_includes_early_end_block_on_user_end(tmp_path):
    item = make_item(item_id="4", title="T", body="B")
    wf = _make_workflow([])
    wf._transcripts["4"] = _make_user_end_transcript()
    ctx = _make_ctx(tmp_path, item=item)
    prompt = wf._build_rewrite_prompt(ctx)
    # The runtime-injected block uses "## Refinement status" as a heading
    assert "## Refinement status" in prompt
    # The ⚠ symbol only appears in the runtime block, not in the static prompt
    assert "⚠ The user ended the interview early" in prompt


# ---------------------------------------------------------------------------
# Section extraction
# ---------------------------------------------------------------------------


def test_extract_title_and_body_from_structured_message():
    message = "### Title\nNew issue title\n\n### Body\nNew body content here."
    sections = extract_sections(message, ["Title", "Body"])
    assert sections["title"] == "New issue title"
    assert sections["body"] == "New body content here."


def test_extract_body_only_when_title_missing_leaves_title_unchanged():
    message = "### Body\nJust the body."
    sections = extract_sections(message, ["Title", "Body"])
    assert "title" not in sections
    assert sections["body"] == "Just the body."


def test_extract_body_only_when_no_structured_sections_uses_full_message_as_body():
    message = "No sections here at all."
    sections = extract_sections(message, ["Title", "Body"])
    assert not sections


# ---------------------------------------------------------------------------
# stages()
# ---------------------------------------------------------------------------


def test_stages_returns_single_rewrite_stage_with_opus_default(tmp_path):
    wf = RefineWorkflow(runner=ScriptedRewriteRunner(""), tracker=AsyncMock())
    item = make_item(item_id="1")
    ctx = _make_ctx(tmp_path, item=item)
    stages = wf.stages(ctx)
    assert len(stages) == 1
    assert stages[0].name == "rewrite"
    assert stages[0].model == "claude-opus-4-6"


def test_stages_honors_cog_refine_rewrite_model_env(tmp_path, monkeypatch):
    monkeypatch.setenv("COG_REFINE_REWRITE_MODEL", "claude-haiku-4-5")
    wf = RefineWorkflow(runner=ScriptedRewriteRunner(""), tracker=AsyncMock())
    item = make_item(item_id="1")
    ctx = _make_ctx(tmp_path, item=item)
    stages = wf.stages(ctx)
    assert stages[0].model == "claude-haiku-4-5"


# ---------------------------------------------------------------------------
# classify_outcome
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_classify_outcome_returns_success_on_accept(tmp_path):
    from cog.workflows.refine import ReviewDecision, ReviewOutcome

    wf = RefineWorkflow(runner=ScriptedRewriteRunner(""), tracker=AsyncMock())
    item = make_item(item_id="5")
    wf._review_outcomes["5"] = ReviewOutcome(
        decision=ReviewDecision.ACCEPT,
        final_body="new body",
        final_title="new title",
    )
    ctx = _make_ctx(tmp_path, item=item)
    outcome = await wf.classify_outcome(ctx, [])
    assert outcome == "success"


@pytest.mark.asyncio
async def test_classify_outcome_returns_noop_on_abandon(tmp_path):
    from cog.workflows.refine import ReviewDecision, ReviewOutcome

    wf = RefineWorkflow(runner=ScriptedRewriteRunner(""), tracker=AsyncMock())
    item = make_item(item_id="6")
    wf._review_outcomes["6"] = ReviewOutcome(
        decision=ReviewDecision.ABANDON,
        final_body="body",
        final_title="title",
    )
    ctx = _make_ctx(tmp_path, item=item)
    outcome = await wf.classify_outcome(ctx, [])
    assert outcome == "noop"
