"""Tests for RalphWorkflow class attributes, lifecycle hooks, and outcome classification."""

import pytest

from cog.checks import RALPH_CHECKS
from cog.core.context import ExecutionContext
from cog.core.outcomes import StageResult
from cog.workflows.ralph import RalphWorkflow
from tests.fakes import EchoRunner, InMemoryStateCache, make_item


def _make_ctx(tmp_path):
    return ExecutionContext(
        project_dir=tmp_path,
        tmp_dir=tmp_path,
        state_cache=InMemoryStateCache(),
        headless=True,
        item=make_item(),
    )


def _make_stage_result(commits_created: int):
    from pathlib import Path

    from cog.core.stage import Stage

    stage = Stage(
        name="build",
        prompt_source=lambda _: "hello",
        model="m",
        runner=EchoRunner(),
    )
    return StageResult(
        stage=stage,
        duration_seconds=0.0,
        cost_usd=0.0,
        exit_status=0,
        final_message="",
        stream_json_path=Path("/dev/null"),
        commits_created=commits_created,
    )


def test_class_attributes():
    assert RalphWorkflow.name == "ralph"
    assert RalphWorkflow.queue_label == "agent-ready"
    assert RalphWorkflow.supports_headless is True
    assert RalphWorkflow.preflight_checks is RALPH_CHECKS


async def test_select_item_raises_not_implemented_mentioning_issue_13(tmp_path):
    wf = RalphWorkflow(EchoRunner())
    with pytest.raises(NotImplementedError) as exc_info:
        await wf.select_item(_make_ctx(tmp_path))
    assert "#13" in str(exc_info.value)


async def test_classify_outcome_success_when_any_commits(tmp_path):
    wf = RalphWorkflow(EchoRunner())
    results = [_make_stage_result(2)]
    outcome = await wf.classify_outcome(_make_ctx(tmp_path), results)
    assert outcome == "success"


async def test_classify_outcome_noop_when_zero_commits(tmp_path):
    wf = RalphWorkflow(EchoRunner())
    results = [_make_stage_result(0)]
    outcome = await wf.classify_outcome(_make_ctx(tmp_path), results)
    assert outcome == "noop"


async def test_classify_outcome_sums_commits_across_stages(tmp_path):
    wf = RalphWorkflow(EchoRunner())
    results = [
        _make_stage_result(0),
        _make_stage_result(1),
        _make_stage_result(0),
    ]
    outcome = await wf.classify_outcome(_make_ctx(tmp_path), results)
    assert outcome == "success"
