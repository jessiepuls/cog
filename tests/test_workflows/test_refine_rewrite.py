"""Tests for RefineWorkflow rewrite stage."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from cog.core.context import ExecutionContext
from cog.workflows.refine import (
    InterviewEnd,
    InterviewTurn,
    RefineWorkflow,
    _extract_title_body,
    _format_transcript_markdown,
)
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


def _set_transcript(wf: RefineWorkflow, tmp_path: Path, item_id: str, transcript: list) -> Path:
    """Set up transcript + path on workflow; returns the path."""
    wf._transcripts[item_id] = transcript
    path = tmp_path / f"interview-{item_id}.md"
    path.write_text("transcript", encoding="utf-8")
    wf._transcript_paths[item_id] = path
    return path


def test_rewrite_prompt_includes_original_body_and_comments(tmp_path):
    from datetime import UTC, datetime

    from cog.core.item import Comment

    comment = Comment(
        author="alice", body="Good point", created_at=datetime(2024, 1, 1, tzinfo=UTC)
    )
    item = make_item(item_id="1", title="My issue", body="Original body", comments=(comment,))
    wf = _make_workflow([])
    _set_transcript(wf, tmp_path, "1", _make_sentinel_transcript())
    ctx = _make_ctx(tmp_path, item=item)
    prompt = wf._build_rewrite_prompt(ctx)
    assert "Original body" in prompt
    assert "alice" in prompt
    assert "Good point" in prompt


def test_rewrite_prompt_does_not_inline_transcript_content(tmp_path):
    item = make_item(item_id="2", title="T", body="B")
    wf = _make_workflow([])
    transcript = _make_sentinel_transcript()
    wf._transcripts["2"] = transcript
    transcript_path = tmp_path / "interview-2.md"
    transcript_path.write_text("transcript content", encoding="utf-8")
    wf._transcript_paths["2"] = transcript_path
    ctx = _make_ctx(tmp_path, item=item)
    prompt = wf._build_rewrite_prompt(ctx)
    assert "What does this item need?" not in prompt
    assert "It needs caching." not in prompt


def test_rewrite_prompt_omits_early_end_block_on_sentinel_end(tmp_path):
    item = make_item(item_id="3", title="T", body="B")
    wf = _make_workflow([])
    _set_transcript(wf, tmp_path, "3", _make_sentinel_transcript())
    ctx = _make_ctx(tmp_path, item=item)
    prompt = wf._build_rewrite_prompt(ctx)
    # The runtime-injected block uses "## Refinement status" as a heading
    assert "## Refinement status" not in prompt


def test_rewrite_prompt_includes_early_end_block_on_user_end(tmp_path):
    item = make_item(item_id="4", title="T", body="B")
    wf = _make_workflow([])
    _set_transcript(wf, tmp_path, "4", _make_user_end_transcript())
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
    sections = _extract_title_body(message)
    assert sections["title"] == "New issue title"
    assert sections["body"] == "New body content here."


def test_extract_body_only_when_title_missing_leaves_title_unchanged():
    message = "### Body\nJust the body."
    sections = _extract_title_body(message)
    assert "title" not in sections
    assert sections["body"] == "Just the body."


def test_extract_body_only_when_no_structured_sections_uses_full_message_as_body():
    message = "No sections here at all."
    sections = _extract_title_body(message)
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
    assert stages[0].model == "claude-opus-4-7"


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


# ---------------------------------------------------------------------------
# Transcript file writing
# ---------------------------------------------------------------------------


def test_format_transcript_markdown_includes_all_turns():
    transcript = _make_sentinel_transcript()
    md = _format_transcript_markdown(transcript)
    assert "## Turn 1 — Assistant" in md
    assert "What does this item need?" in md
    assert "## Turn 1 — User" in md
    assert "It needs caching." in md
    assert "## Turn 2 — Assistant" in md
    assert "Got it." in md


def test_format_transcript_markdown_omits_user_section_when_none():
    transcript = _make_user_end_transcript()
    md = _format_transcript_markdown(transcript)
    assert "## Turn 1 — Assistant" in md
    assert "## Turn 1 — User" not in md


@pytest.mark.asyncio
async def test_pre_stages_writes_transcript_to_ctx_tmp_dir_with_expected_filename(tmp_path):
    from unittest.mock import AsyncMock

    from tests.fakes import RecordingEventSink, ScriptedInputProvider, ScriptedInterviewRunner

    _SENTINEL = "<<interview-complete>>"
    responses = [("All done! " + _SENTINEL, 0.01)]
    runner = ScriptedInterviewRunner(responses)
    sink = RecordingEventSink()
    provider = ScriptedInputProvider([])
    item = make_item(item_id="77", title="Test item")
    ctx = ExecutionContext(
        project_dir=tmp_path,
        tmp_dir=tmp_path,
        state_cache=InMemoryStateCache(),
        headless=False,
        item=item,
        event_sink=sink,
        input_provider=provider,
    )
    wf = RefineWorkflow(runner=runner, tracker=AsyncMock())
    await wf.pre_stages(ctx)

    expected = tmp_path / "interview-77.md"
    assert expected.exists()


@pytest.mark.asyncio
async def test_transcript_file_contains_all_turns_in_markdown_format(tmp_path):
    from unittest.mock import AsyncMock

    from tests.fakes import RecordingEventSink, ScriptedInputProvider, ScriptedInterviewRunner

    _SENTINEL = "<<interview-complete>>"
    responses = [
        ("First question?", 0.01),
        ("All done! " + _SENTINEL, 0.02),
    ]
    runner = ScriptedInterviewRunner(responses)
    sink = RecordingEventSink()
    provider = ScriptedInputProvider(["my answer"])
    item = make_item(item_id="88", title="Test item")
    ctx = ExecutionContext(
        project_dir=tmp_path,
        tmp_dir=tmp_path,
        state_cache=InMemoryStateCache(),
        headless=False,
        item=item,
        event_sink=sink,
        input_provider=provider,
    )
    wf = RefineWorkflow(runner=runner, tracker=AsyncMock())
    await wf.pre_stages(ctx)

    content = (tmp_path / "interview-88.md").read_text(encoding="utf-8")
    assert "## Turn 1 — Assistant" in content
    assert "First question?" in content
    assert "## Turn 1 — User" in content
    assert "my answer" in content
    assert "## Turn 2 — Assistant" in content


def test_rewrite_prompt_references_actual_transcript_path(tmp_path):
    item = make_item(item_id="55", title="T", body="B")
    wf = _make_workflow([])
    transcript_path = _set_transcript(wf, tmp_path, "55", _make_sentinel_transcript())
    ctx = _make_ctx(tmp_path, item=item)
    prompt = wf._build_rewrite_prompt(ctx)
    assert str(transcript_path) in prompt
