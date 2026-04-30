import sys
import time

from cog.core.context import ExecutionContext
from cog.core.errors import StageError
from cog.core.runner import (
    AssistantTextEvent,
    RunEvent,
    StageEndEvent,
    StageStartEvent,
    StatusEvent,
    ToolUseEvent,
)
from cog.core.workflow import IterationOutcome, StageExecutor, Workflow
from cog.loop import LoopState, fresh_iteration_context


class StderrEventSink:
    """Formats RunEvents to stderr in ralph-style output. No color (stderr may be redirected)."""

    async def emit(self, event: RunEvent) -> None:
        if isinstance(event, StageStartEvent):
            sys.stderr.write(f"\n=== {event.stage_name} ({event.model}) ===\n")
        elif isinstance(event, StageEndEvent):
            sys.stderr.write(
                f"=== {event.stage_name} complete: "
                f"${event.cost_usd:.3f} (exit {event.exit_status}) ===\n"
            )
        elif isinstance(event, ToolUseEvent):
            preview = event.input.get("command") or event.input.get("file_path") or ""
            sys.stderr.write(f"  > {event.tool}: {preview}\n")
        elif isinstance(event, AssistantTextEvent):
            for line in event.text.splitlines():
                sys.stderr.write(f"  {line}\n")
        elif isinstance(event, StatusEvent):
            sys.stderr.write(f"-- {event.message}\n")
        # ResultEvent is internal to runner; StageEndEvent carries the data.
        # Any other event type (future additions) is silently dropped.
        sys.stderr.flush()


async def run_headless(
    workflow: Workflow,
    base_ctx: ExecutionContext,
    *,
    loop: bool = False,
    max_iterations: int | None = None,
) -> int:
    """Run workflow iterations in headless mode. Returns 0/1 exit code."""
    base_ctx.event_sink = StderrEventSink()
    state = LoopState()
    start = time.monotonic()
    while True:
        if max_iterations is not None and state.iteration >= max_iterations:
            break
        state.iteration += 1
        if loop:
            sys.stderr.write(f"\n═══ iteration {state.iteration} ═══\n")
        ctx = fresh_iteration_context(
            base_ctx,
            preserve_item=state.iteration == 1 and base_ctx.item is not None,
        )
        try:
            results = await StageExecutor().run(workflow, ctx)
        except StageError as e:
            await workflow.iteration_end(ctx, IterationOutcome.error)
            duration = time.monotonic() - start
            sys.stderr.write(
                f"\niteration FAILED: stage {e.stage.name!r} failed after {duration:.0f}s\n"
            )
            return 1
        except Exception as e:  # noqa: BLE001 - surface any unexpected failure
            await workflow.iteration_end(ctx, IterationOutcome.exception)
            duration = time.monotonic() - start
            sys.stderr.write(
                f"\niteration FAILED: {type(e).__name__}: {e} (after {duration:.0f}s)\n"
            )
            return 1
        if not results:
            state.iteration -= 1  # empty-queue probe: don't count as an iteration
            break
        commits = sum(r.commits_created for r in results)
        it_outcome = IterationOutcome.success if commits > 0 else IterationOutcome.noop
        await workflow.iteration_end(ctx, it_outcome)
        state.cumulative_cost_usd += sum(r.cost_usd for r in results)
        iter_duration = sum(r.duration_seconds for r in results)
        outcome = "success" if commits > 0 else "no-op"
        sys.stderr.write(
            f"iteration complete: outcome={outcome}, "
            f"cost=${sum(r.cost_usd for r in results):.3f}, "
            f"duration={iter_duration:.0f}s\n"
        )
        if not loop:
            break

    total = time.monotonic() - start
    if loop:
        sys.stderr.write(
            f"\nloop complete: {state.iteration} iteration(s), "
            f"total cost ${state.cumulative_cost_usd:.3f}, "
            f"total duration {total:.0f}s\n"
        )
    return 0
