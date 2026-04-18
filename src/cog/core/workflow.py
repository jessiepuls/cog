from __future__ import annotations

import time
from abc import ABC, abstractmethod
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Literal

from cog.core.context import ExecutionContext
from cog.core.errors import StageError
from cog.core.item import Item
from cog.core.outcomes import Outcome, StageResult
from cog.core.preflight import PreflightCheck
from cog.core.runner import ResultEvent, RunResult, StageEndEvent, StageStartEvent
from cog.core.stage import Stage

if TYPE_CHECKING:
    from textual.widget import Widget


class Workflow(ABC):
    name: ClassVar[str]
    queue_label: ClassVar[str]  # "agent-ready" / "needs-refinement"
    supports_headless: ClassVar[bool]  # no default — subclasses declare
    preflight_checks: ClassVar[Sequence[PreflightCheck]] = ()
    content_widget_cls: ClassVar[type[Widget] | None] = None

    @abstractmethod
    async def select_item(self, ctx: ExecutionContext) -> Item | None:
        """Return next item to process. None = queue empty → executor exits."""

    async def pre_stages(self, ctx: ExecutionContext) -> None:
        return

    @abstractmethod
    def stages(self, ctx: ExecutionContext) -> list[Stage]:
        """Return stages to run. Sync because stages are data; executor awaits execution."""

    async def post_stages(self, ctx: ExecutionContext, results: list[StageResult]) -> None:
        return

    @abstractmethod
    async def classify_outcome(self, ctx: ExecutionContext, results: list[StageResult]) -> Outcome:
        """Workflow-specific: ralph counts commits; refine checks whether body was updated."""

    async def finalize_success(self, ctx: ExecutionContext, results: list[StageResult]) -> None:
        return

    async def finalize_noop(self, ctx: ExecutionContext, results: list[StageResult]) -> None:
        return

    async def finalize_error(
        self, ctx: ExecutionContext, error: Exception, results: list[StageResult]
    ) -> None:
        return

    async def write_report(
        self,
        ctx: ExecutionContext,
        results: list[StageResult],
        outcome: Literal["success", "noop", "error"],
        *,
        error: Exception | None = None,
    ) -> Path | None:
        """Default: no report. Workflows override to write a markdown file to
        project_state_dir(ctx.project_dir) / 'reports' / '<ts>-<workflow>-<item>.md'."""
        return None


class StageExecutor:
    """Runs one workflow iteration against an ExecutionContext."""

    async def run(self, workflow: Workflow, ctx: ExecutionContext) -> list[StageResult]:
        if ctx.item is None:
            item = await workflow.select_item(ctx)
            if item is None:
                return []
            ctx.item = item
        results: list[StageResult] = []
        try:
            await workflow.pre_stages(ctx)
            for stage in workflow.stages(ctx):
                results.append(await self._run_stage(stage, ctx))
            await workflow.post_stages(ctx, results)
            outcome = await workflow.classify_outcome(ctx, results)
            if outcome == "success":
                await workflow.finalize_success(ctx, results)
            else:
                await workflow.finalize_noop(ctx, results)
        except Exception as e:
            await workflow.finalize_error(ctx, e, results)
            raise
        return results

    async def _run_stage(self, stage: Stage, ctx: ExecutionContext) -> StageResult:
        sink = ctx.event_sink
        if sink is not None:
            await sink.emit(StageStartEvent(stage_name=stage.name, model=stage.model))
        start = time.monotonic()
        run_result: RunResult | None = None
        try:
            prompt = stage.prompt_source(ctx)
            async for event in stage.runner.stream(prompt, model=stage.model):
                if isinstance(event, ResultEvent):
                    run_result = event.result
                elif sink is not None:
                    await sink.emit(event)
        except Exception as e:
            raise StageError(stage, cause=e) from e
        assert run_result is not None, "runner must emit a ResultEvent before finishing"
        duration = time.monotonic() - start
        stage_result = StageResult(
            stage=stage,
            duration_seconds=duration,
            cost_usd=run_result.total_cost_usd,
            exit_status=run_result.exit_status,
            final_message=run_result.final_message,
            stream_json_path=run_result.stream_json_path,
            commits_created=0,  # stub: git integration lands in #13
        )
        if sink is not None:
            await sink.emit(
                StageEndEvent(
                    stage_name=stage.name,
                    cost_usd=run_result.total_cost_usd,
                    exit_status=run_result.exit_status,
                )
            )
        if run_result.exit_status != 0:
            raise StageError(stage, stage_result)
        return stage_result
