"""Tests for StageExecutor behavior."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from cog.core.context import ExecutionContext
from cog.core.errors import GitError, StageError
from cog.core.item import Item
from cog.core.outcomes import Outcome, StageResult
from cog.core.runner import AgentRunner, ResultEvent, RunEvent, RunResult
from cog.core.stage import Stage
from cog.core.workflow import StageExecutor, Workflow
from tests.fakes import EchoRunner, ExitNonZeroRunner, FailingRunner


def _make_item() -> Item:
    return Item(
        tracker_id="test",
        item_id="1",
        title="test item",
        body="",
        labels=(),
        comments=(),
        state="open",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        url="",
    )


class _SimpleWorkflow(Workflow):
    """Minimal workflow with configurable outcome and finalize tracking."""

    name = "simple"
    queue_label = "test"
    supports_headless = True

    def __init__(
        self,
        runner: AgentRunner,
        *,
        outcome: Outcome = "success",
        item: Item | None = None,
    ) -> None:
        self._runner = runner
        self._outcome = outcome
        self._item = item or _make_item()
        self.finalize_called: str | None = None

    async def select_item(self, ctx: ExecutionContext) -> Item | None:
        return self._item

    def stages(self, ctx: ExecutionContext) -> list[Stage]:
        return [Stage(name="s1", prompt_source=lambda _: "hello", model="m", runner=self._runner)]

    async def classify_outcome(self, ctx: ExecutionContext, results: list[StageResult]) -> Outcome:
        return self._outcome

    async def finalize_success(self, ctx: ExecutionContext, results: list[StageResult]) -> None:
        self.finalize_called = "success"

    async def finalize_noop(self, ctx: ExecutionContext, results: list[StageResult]) -> None:
        self.finalize_called = "noop"

    async def finalize_error(
        self, ctx: ExecutionContext, error: Exception, results: list[StageResult]
    ) -> None:
        self.finalize_called = "error"


class _EmptyQueueWorkflow(_SimpleWorkflow):
    async def select_item(self, ctx: ExecutionContext) -> Item | None:
        return None


class _FailRunner(AgentRunner):
    async def stream(self, prompt: str, *, model: str) -> AsyncIterator[RunEvent]:
        yield ResultEvent(
            result=RunResult(
                final_message="failed",
                total_cost_usd=0.0,
                exit_status=1,
                stream_json_path=Path("/dev/null"),
                duration_seconds=0.0,
            )
        )


class _RaisingRunner(AgentRunner):
    async def stream(self, prompt: str, *, model: str) -> AsyncIterator[RunEvent]:
        raise RuntimeError("runner exploded")
        yield  # type: ignore[misc]


async def test_happy_path_stages_run_in_order(ctx_factory, echo_runner):
    wf = _SimpleWorkflow(echo_runner)
    results = await StageExecutor().run(wf, ctx_factory())
    assert len(results) == 1
    assert results[0].final_message == "hello"
    assert results[0].exit_status == 0
    assert wf.finalize_called == "success"


async def test_select_item_none_exits_cleanly(ctx_factory, echo_runner):
    wf = _EmptyQueueWorkflow(echo_runner)
    results = await StageExecutor().run(wf, ctx_factory())
    assert results == []
    assert wf.finalize_called is None


async def test_stage_nonzero_exit_raises_stage_error(ctx_factory):
    wf = _SimpleWorkflow(_FailRunner())
    with pytest.raises(StageError) as exc_info:
        await StageExecutor().run(wf, ctx_factory())
    err = exc_info.value
    assert err.stage.name == "s1"
    assert err.result is not None
    assert err.result.exit_status == 1
    assert err.cause is None
    assert wf.finalize_called == "error"


async def test_runner_raises_wraps_in_stage_error_with_cause(ctx_factory):
    wf = _SimpleWorkflow(_RaisingRunner())
    with pytest.raises(StageError) as exc_info:
        await StageExecutor().run(wf, ctx_factory())
    err = exc_info.value
    assert err.stage.name == "s1"
    assert err.result is None
    assert isinstance(err.cause, RuntimeError)
    assert wf.finalize_called == "error"


async def test_classify_noop_calls_finalize_noop(ctx_factory, echo_runner):
    wf = _SimpleWorkflow(echo_runner, outcome="noop")
    await StageExecutor().run(wf, ctx_factory())
    assert wf.finalize_called == "noop"


async def test_executor_populates_commits_created(ctx_factory, echo_runner):
    wf = _SimpleWorkflow(echo_runner)
    with (
        patch("cog.core.workflow.git.current_head_sha", new=AsyncMock(return_value="abc123")),
        patch("cog.core.workflow.git.commits_between", new=AsyncMock(return_value=3)),
    ):
        results = await StageExecutor().run(wf, ctx_factory())
    assert results[0].commits_created == 3


async def test_executor_commits_created_zero_when_not_git_repo(ctx_factory, echo_runner):
    wf = _SimpleWorkflow(echo_runner)
    with patch(
        "cog.core.workflow.git.current_head_sha",
        new=AsyncMock(side_effect=GitError("not a git repo")),
    ):
        results = await StageExecutor().run(wf, ctx_factory())
    assert results[0].commits_created == 0


async def test_executor_commits_created_zero_when_counting_fails(ctx_factory, echo_runner):
    wf = _SimpleWorkflow(echo_runner)
    with (
        patch("cog.core.workflow.git.current_head_sha", new=AsyncMock(return_value="abc123")),
        patch(
            "cog.core.workflow.git.commits_between",
            new=AsyncMock(side_effect=GitError("counting failed")),
        ),
    ):
        results = await StageExecutor().run(wf, ctx_factory())
    assert results[0].commits_created == 0


# --- tolerate_failure regression guards ---


def _make_tolerate_stage(runner, *, tolerate: bool) -> Stage:
    return Stage(
        name="s1",
        prompt_source=lambda _: "hello",
        model="m",
        runner=runner,
        tolerate_failure=tolerate,
    )


async def test_tolerate_failure_false_raises_on_runner_exception(ctx_factory):
    executor = StageExecutor()
    stage = _make_tolerate_stage(FailingRunner(), tolerate=False)
    with pytest.raises(StageError) as exc_info:
        await executor._run_stage(stage, ctx_factory())
    assert exc_info.value.stage.name == "s1"
    assert isinstance(exc_info.value.cause, RuntimeError)


async def test_tolerate_failure_false_raises_on_nonzero_exit(ctx_factory):
    executor = StageExecutor()
    stage = _make_tolerate_stage(ExitNonZeroRunner(1), tolerate=False)
    with pytest.raises(StageError) as exc_info:
        await executor._run_stage(stage, ctx_factory())
    assert exc_info.value.result is not None
    assert exc_info.value.result.exit_status == 1


async def test_tolerate_failure_true_runner_exception_continues(ctx_factory):
    executor = StageExecutor()
    stage = _make_tolerate_stage(FailingRunner(), tolerate=True)
    result = await executor._run_stage(stage, ctx_factory())
    assert result.error is not None


async def test_tolerate_failure_true_nonzero_exit_continues(ctx_factory):
    executor = StageExecutor()
    stage = _make_tolerate_stage(ExitNonZeroRunner(1), tolerate=True)
    result = await executor._run_stage(stage, ctx_factory())
    assert result.error is not None


async def test_tolerate_failure_runner_exception_result_has_exit_status_minus_one(ctx_factory):
    executor = StageExecutor()
    stage = _make_tolerate_stage(FailingRunner(), tolerate=True)
    result = await executor._run_stage(stage, ctx_factory())
    assert result.exit_status == -1


async def test_tolerate_failure_runner_exception_stores_original_exception(ctx_factory):
    exc = RuntimeError("boom")
    executor = StageExecutor()
    stage = _make_tolerate_stage(FailingRunner(exc), tolerate=True)
    result = await executor._run_stage(stage, ctx_factory())
    assert result.error is exc


async def test_tolerate_failure_nonzero_exit_stores_stage_error_as_error(ctx_factory):
    executor = StageExecutor()
    stage = _make_tolerate_stage(ExitNonZeroRunner(2), tolerate=True)
    result = await executor._run_stage(stage, ctx_factory())
    assert isinstance(result.error, StageError)
    assert result.exit_status == 2


async def test_subsequent_stages_run_after_tolerated_failure(ctx_factory):
    """Middle stage fails with tolerate_failure=True; executor continues to all 3 stages."""
    runner_ok = EchoRunner()
    runner_fail = FailingRunner()

    stages = [
        Stage(name="first", prompt_source=lambda _: "a", model="m", runner=runner_ok),
        Stage(
            name="middle",
            prompt_source=lambda _: "b",
            model="m",
            runner=runner_fail,
            tolerate_failure=True,
        ),
        Stage(name="last", prompt_source=lambda _: "c", model="m", runner=runner_ok),
    ]

    class _ThreeStageWorkflow(Workflow):
        name = "three"
        queue_label = "test"
        supports_headless = True

        async def select_item(self, ctx):
            return _make_item()

        def stages(self, ctx):
            return stages

        async def classify_outcome(self, ctx, results):
            return "noop"

    results = await StageExecutor().run(_ThreeStageWorkflow(), ctx_factory())
    assert len(results) == 3
    assert results[0].stage.name == "first"
    assert results[1].stage.name == "middle"
    assert results[2].stage.name == "last"
    assert results[1].error is not None
    assert results[0].error is None
    assert results[2].error is None
