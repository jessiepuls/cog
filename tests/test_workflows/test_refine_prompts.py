"""Tests for refine rewrite prompt assembly — on-demand transcript pattern."""

from pathlib import Path
from unittest.mock import AsyncMock

from cog.core.context import ExecutionContext
from cog.workflows.refine import InterviewEnd, InterviewTurn, RefineWorkflow
from tests.fakes import InMemoryStateCache, ScriptedRewriteRunner, make_item


def _make_ctx(tmp_path: Path, item=None) -> ExecutionContext:
    return ExecutionContext(
        project_dir=tmp_path,
        tmp_dir=tmp_path,
        state_cache=InMemoryStateCache(),
        headless=False,
        item=item,
    )


def _make_sentinel_transcript() -> list[InterviewTurn]:
    return [
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


def _setup_workflow_with_transcript(tmp_path: Path, item_id: str) -> tuple[RefineWorkflow, Path]:
    wf = RefineWorkflow(runner=ScriptedRewriteRunner(""), tracker=AsyncMock())
    wf._transcripts[item_id] = _make_sentinel_transcript()
    transcript_path = tmp_path / f"interview-{item_id}.md"
    transcript_path.write_text("transcript content", encoding="utf-8")
    wf._transcript_paths[item_id] = transcript_path
    return wf, transcript_path


def test_refine_rewrite_prompt_tells_claude_to_read_transcript_file(tmp_path):
    item = make_item(item_id="10", title="T", body="B")
    wf, _ = _setup_workflow_with_transcript(tmp_path, "10")
    ctx = _make_ctx(tmp_path, item=item)
    prompt = wf._build_rewrite_prompt(ctx)
    assert "Read" in prompt or "read" in prompt


def test_refine_rewrite_prompt_points_to_tmp_dir_path(tmp_path):
    item = make_item(item_id="11", title="T", body="B")
    wf, transcript_path = _setup_workflow_with_transcript(tmp_path, "11")
    ctx = _make_ctx(tmp_path, item=item)
    prompt = wf._build_rewrite_prompt(ctx)
    assert str(transcript_path) in prompt


def test_refine_rewrite_prompt_does_not_inline_transcript_content(tmp_path):
    item = make_item(item_id="12", title="T", body="B")
    wf, _ = _setup_workflow_with_transcript(tmp_path, "12")
    ctx = _make_ctx(tmp_path, item=item)
    prompt = wf._build_rewrite_prompt(ctx)
    assert "What does this need?" not in prompt
    assert "It needs caching." not in prompt
    assert "Got it." not in prompt


def test_refine_rewrite_prompt_includes_partial_read_guidance(tmp_path):
    item = make_item(item_id="13", title="T", body="B")
    wf, _ = _setup_workflow_with_transcript(tmp_path, "13")
    ctx = _make_ctx(tmp_path, item=item)
    prompt = wf._build_rewrite_prompt(ctx)
    assert "grep" in prompt.lower() or "partial" in prompt.lower()
