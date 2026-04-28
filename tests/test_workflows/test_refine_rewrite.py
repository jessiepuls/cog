"""Tests for RefineWorkflow rewrite stage."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from cog.checks import REFINE_CHECKS
from cog.core.context import ExecutionContext
from cog.workflows.refine import (
    _INTERVIEW_COMPLETE,
    InterviewEnd,
    InterviewTurn,
    RefineWorkflow,
    _extract_title_body,
    _format_transcript_markdown,
)
from tests.fakes import (
    InMemoryStateCache,
    RecordingEventSink,
    ScriptedInputProvider,
    ScriptedInterviewRunner,
    ScriptedRewriteRunner,
    make_item,
)


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
    transcript = _make_sentinel_transcript()
    wf._transcripts["1"] = transcript
    _setup_transcript_path(tmp_path, "1")
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
    _setup_transcript_path(tmp_path, "2")
    ctx = _make_ctx(tmp_path, item=item)
    prompt = wf._build_rewrite_prompt(ctx)
    assert "What does this item need?" not in prompt
    assert "It needs caching." not in prompt


def _setup_transcript_path(tmp_path: Path, item_id: str) -> None:
    path = tmp_path / ".cog" / f"interview-{item_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("stub", encoding="utf-8")


def test_rewrite_prompt_omits_early_end_block_on_sentinel_end(tmp_path):
    item = make_item(item_id="3", title="T", body="B")
    wf = _make_workflow([])
    wf._transcripts["3"] = _make_sentinel_transcript()
    _setup_transcript_path(tmp_path, "3")
    ctx = _make_ctx(tmp_path, item=item)
    prompt = wf._build_rewrite_prompt(ctx)
    # The runtime-injected block uses "## Refinement status" as a heading
    assert "## Refinement status" not in prompt


def test_rewrite_prompt_includes_early_end_block_on_user_end(tmp_path):
    item = make_item(item_id="4", title="T", body="B")
    wf = _make_workflow([])
    wf._transcripts["4"] = _make_user_end_transcript()
    _setup_transcript_path(tmp_path, "4")
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


def test_extract_body_preserves_nested_h3_subsections():
    message = (
        "### Title\nT\n\n"
        "### Body\n"
        "## Problem\nP\n\n"
        "## Scope\n"
        "### onDestroy placement\n"
        "Add a second onDestroy block.\n\n"
        "### Test approach\n"
        "Use unmount() from render().\n"
    )
    sections = _extract_title_body(message)
    assert sections["title"] == "T"
    assert "### onDestroy placement" in sections["body"]
    assert "Use unmount() from render()." in sections["body"]


def test_extract_treats_repeat_title_or_body_h3_as_section_delimiter():
    message = "### Title\nFirst title\n\n### Body\nFirst body content.\n\n### Title\nSecond title\n"
    sections = _extract_title_body(message)
    assert sections["title"] == "Second title"
    assert sections["body"] == "First body content."


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
# _format_transcript_markdown
# ---------------------------------------------------------------------------


def test_format_transcript_markdown_includes_all_turns():
    turns = [
        InterviewTurn(
            assistant_message="First question",
            user_message="First answer",
            cost_usd=0.1,
            duration_seconds=1.0,
        ),
        InterviewTurn(
            assistant_message="Second question",
            user_message=None,
            cost_usd=0.1,
            duration_seconds=1.0,
            end=InterviewEnd.SENTINEL,
        ),
    ]
    md = _format_transcript_markdown(turns)
    assert "First question" in md
    assert "First answer" in md
    assert "Second question" in md


def test_format_transcript_markdown_uses_section_headings():
    turns = [
        InterviewTurn(
            assistant_message="Hello",
            user_message="Hi",
            cost_usd=0.0,
            duration_seconds=0.0,
        )
    ]
    md = _format_transcript_markdown(turns)
    assert "## Turn 1 — Assistant" in md
    assert "## Turn 1 — User" in md


def test_format_transcript_markdown_omits_user_section_when_no_user_message():
    turns = [
        InterviewTurn(
            assistant_message="Done",
            user_message=None,
            cost_usd=0.0,
            duration_seconds=0.0,
            end=InterviewEnd.SENTINEL,
        )
    ]
    md = _format_transcript_markdown(turns)
    assert "## Turn 1 — Assistant" in md
    assert "User" not in md


# ---------------------------------------------------------------------------
# pre_stages — transcript file creation
# ---------------------------------------------------------------------------


def _make_pre_stages_ctx(tmp_path: Path, item_id: str) -> ExecutionContext:
    item = make_item(item_id=item_id, title="Test item", body="Body")
    tmp_dir = tmp_path / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    return ExecutionContext(
        project_dir=tmp_path,
        tmp_dir=tmp_dir,
        state_cache=InMemoryStateCache(),
        headless=False,
        item=item,
        event_sink=RecordingEventSink(),
        input_provider=ScriptedInputProvider([]),
    )


@pytest.mark.asyncio
async def test_pre_stages_writes_transcript_to_dotcog_dir_with_expected_filename(tmp_path):
    runner = ScriptedInterviewRunner([(f"Question\n{_INTERVIEW_COMPLETE}", 0.1)])
    wf = RefineWorkflow(runner=runner, tracker=AsyncMock())
    ctx = _make_pre_stages_ctx(tmp_path, "20")
    await wf.pre_stages(ctx)
    assert (ctx.project_dir / ".cog" / "interview-20.md").exists()


@pytest.mark.asyncio
async def test_transcript_file_contains_all_turns_in_markdown_format(tmp_path):
    runner = ScriptedInterviewRunner([(f"My question\n{_INTERVIEW_COMPLETE}", 0.1)])
    wf = RefineWorkflow(runner=runner, tracker=AsyncMock())
    ctx = _make_pre_stages_ctx(tmp_path, "21")
    await wf.pre_stages(ctx)
    content = (ctx.project_dir / ".cog" / "interview-21.md").read_text(encoding="utf-8")
    assert "## Turn 1 — Assistant" in content
    assert "My question" in content


@pytest.mark.asyncio
async def test_rewrite_prompt_references_sandbox_transcript_path(tmp_path):
    runner = ScriptedInterviewRunner([(f"Q\n{_INTERVIEW_COMPLETE}", 0.1)])
    wf = RefineWorkflow(runner=runner, tracker=AsyncMock())
    ctx = _make_pre_stages_ctx(tmp_path, "22")
    await wf.pre_stages(ctx)
    prompt = wf._build_rewrite_prompt(ctx)
    assert "/work/.cog/interview-22.md" in prompt


def test_refine_checks_do_not_contain_default_branch():
    names = {c.name for c in REFINE_CHECKS}
    assert "default_branch" not in names


def test_refine_checks_do_not_contain_clean_tree():
    names = {c.name for c in REFINE_CHECKS}
    assert "clean_tree" not in names
