"""Tests for StageExecutor behavior."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from cog.core.context import ExecutionContext
from cog.core.errors import StageError
from cog.core.item import Item
from cog.core.outcomes import Outcome, StageResult
from cog.core.runner import AgentRunner, ResultEvent, RunEvent, RunResult
from cog.core.stage import Stage
from cog.core.workflow import StageExecutor, Workflow


def _make_item() -> Item:
    return Item(
        tracker_id="test",
        item_id="1",
        title="test item",
        body="",
        labels=(),
        comments=(),
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
