"""Tests for RefineWorkflow rewrite prompt assembly (on-demand transcript pattern)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

from cog.core.context import ExecutionContext
from cog.workflows.refine import InterviewEnd, InterviewTurn, RefineWorkflow
from tests.fakes import InMemoryStateCache, ScriptedRewriteRunner, make_item


def _make_ctx(tmp_path: Path, item_id: str) -> ExecutionContext:
    item = make_item(item_id=item_id, title="My item", body="Body text")
    tmp_dir = tmp_path / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    return ExecutionContext(
        project_dir=tmp_path,
        tmp_dir=tmp_dir,
        state_cache=InMemoryStateCache(),
        headless=False,
        item=item,
    )


def _make_workflow_with_transcript(
    tmp_path: Path, item_id: str
) -> tuple[RefineWorkflow, ExecutionContext]:
    wf = RefineWorkflow(runner=ScriptedRewriteRunner(""), tracker=AsyncMock())
    turns = [
        InterviewTurn(
            assistant_message="What does this need?",
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
    wf._transcripts[item_id] = turns
    ctx = _make_ctx(tmp_path, item_id)
    transcript_path = tmp_path / ".cog" / f"interview-{item_id}.md"
    transcript_path.parent.mkdir(parents=True, exist_ok=True)
    transcript_path.write_text("stub content", encoding="utf-8")
    return wf, ctx


def test_refine_rewrite_prompt_tells_claude_to_read_transcript_file(tmp_path):
    wf, ctx = _make_workflow_with_transcript(tmp_path, "30")
    prompt = wf._build_rewrite_prompt(ctx)
    assert "Read" in prompt or "read" in prompt
    assert "interview-30.md" in prompt


def test_refine_rewrite_prompt_points_to_sandbox_path(tmp_path):
    wf, ctx = _make_workflow_with_transcript(tmp_path, "31")
    prompt = wf._build_rewrite_prompt(ctx)
    assert "/work/.cog/interview-31.md" in prompt


def test_refine_rewrite_prompt_does_not_inline_transcript_content(tmp_path):
    wf, ctx = _make_workflow_with_transcript(tmp_path, "32")
    prompt = wf._build_rewrite_prompt(ctx)
    assert "What does this need?" not in prompt
    assert "It needs caching." not in prompt
    assert "Got it." not in prompt


def test_refine_rewrite_prompt_includes_partial_read_guidance(tmp_path):
    wf, ctx = _make_workflow_with_transcript(tmp_path, "33")
    prompt = wf._build_rewrite_prompt(ctx)
    assert "grep" in prompt or "partial" in prompt
