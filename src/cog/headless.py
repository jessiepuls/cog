import sys
import time

from cog.core.context import ExecutionContext
from cog.core.errors import StageError
from cog.core.runner import (
    AssistantTextEvent,
    RunEvent,
    StageEndEvent,
    StageStartEvent,
    ToolUseEvent,
)
from cog.core.workflow import StageExecutor, Workflow


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
        # ResultEvent is internal to runner; StageEndEvent carries the data.
        # Any other event type (future additions) is silently dropped.
        sys.stderr.flush()


async def run_headless(
    workflow: Workflow,
    ctx: ExecutionContext,
    *,
    loop: bool = False,  # unused here; the #16 loop wrapper consumes this
) -> int:
    """Run one workflow iteration in headless mode. Returns 0/1 exit code."""
    ctx.event_sink = StderrEventSink()
    # ctx.input_provider stays None — headless cannot prompt

    start = time.monotonic()
    try:
        results = await StageExecutor().run(workflow, ctx)
    except StageError as e:
        duration = time.monotonic() - start
        sys.stderr.write(
            f"\niteration FAILED: stage {e.stage.name!r} failed after {duration:.0f}s\n"
        )
        return 1
    except Exception as e:  # noqa: BLE001 - surface any unexpected failure
        duration = time.monotonic() - start
        sys.stderr.write(f"\niteration FAILED: {type(e).__name__}: {e} (after {duration:.0f}s)\n")
        return 1

    duration = time.monotonic() - start
    total_cost = sum(r.cost_usd for r in results)
    outcome = "success" if results else "no-op"
    sys.stderr.write(
        f"\niteration complete: outcome={outcome}, "
        f"cost=${total_cost:.3f}, duration={duration:.0f}s\n"
    )
    return 0
