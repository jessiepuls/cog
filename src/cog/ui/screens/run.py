"""RunScreen — multi-iteration lifecycle (#16).

Used by the CLI path (`cog ralph --item N` / `cog refine --item N` without
`--headless`). The shell path (`cog` → refine/ralph view) hosts workflows
inline in view widgets instead; those views reuse the stage-breakdown
helpers exported from this module (`StageSummary`, `StageCountingSink`).
"""

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from textual.app import ComposeResult
from textual.screen import Screen
from textual.widget import Widget
from textual.widgets import Footer, Header, Static
from textual.worker import Worker

from cog.core.context import ExecutionContext
from cog.core.runner import RunEvent, StageEndEvent, StageStartEvent
from cog.core.workflow import StageExecutor, Workflow
from cog.loop import LoopState, fresh_iteration_context


@dataclass
class StageSummary:
    name: str
    model: str = ""
    cost_usd: float = 0.0
    duration_s: float = 0.0
    turns: int = 0
    status: Literal["running", "completed", "failed"] = "running"


class StageCountingSink:
    """Forwards events to an inner sink, accumulates cost via a callback, and
    builds a per-stage breakdown usable for completion / failure panels.

    Used by both RunScreen (CLI path) and the shell's ralph/refine views
    (which embed their own event sinks + footers). Callback decoupling lets
    each host update its own cost counter / footer without the sink knowing
    about its host type.
    """

    def __init__(
        self,
        inner: object,
        *,
        on_cost: Callable[[float], None],
    ) -> None:
        self._inner = inner
        self._on_cost = on_cost
        self._stages: list[StageSummary] = []
        self._stage_starts: dict[str, float] = {}

    @property
    def stages(self) -> list[StageSummary]:
        return self._stages

    def mark_running_stages_failed(self) -> None:
        now = time.monotonic()
        for s in self._stages:
            if s.status == "running":
                s.status = "failed"
                start = self._stage_starts.get(s.name)
                if start is not None:
                    s.duration_s = now - start

    async def emit(self, event: RunEvent) -> None:
        if hasattr(self._inner, "emit"):
            await self._inner.emit(event)
        if isinstance(event, StageStartEvent):
            self._stages.append(StageSummary(name=event.stage_name, model=event.model))
            self._stage_starts[event.stage_name] = time.monotonic()
        elif isinstance(event, StageEndEvent):
            self._on_cost(event.cost_usd)
            running = next(
                (s for s in self._stages if s.name == event.stage_name and s.status == "running"),
                None,
            )
            if running is not None:
                start = self._stage_starts.pop(event.stage_name, None)
                running.duration_s = (time.monotonic() - start) if start is not None else 0.0
                running.cost_usd = event.cost_usd
                running.turns = 1
                running.status = "completed" if event.exit_status == 0 else "failed"
                return
            # Orphan StageEnd — e.g. refine interview turns (no StageStartEvent
            # is emitted per turn). Aggregate into an existing entry or create
            # a fresh completed one.
            existing = next((s for s in self._stages if s.name == event.stage_name), None)
            if existing is not None:
                existing.cost_usd += event.cost_usd
                existing.turns += 1
            else:
                self._stages.append(
                    StageSummary(
                        name=event.stage_name,
                        cost_usd=event.cost_usd,
                        turns=1,
                        status="completed",
                    )
                )


def format_stage_duration(seconds: float) -> str:
    secs = int(seconds)
    if secs < 60:
        return f"{secs}s"
    return f"{secs // 60}m{secs % 60:02d}s"


def format_stage_line(stage: StageSummary) -> str:
    parts = []
    if stage.status == "failed":
        parts.append("[red]✗[/red]")
    parts.append(stage.name)
    parts.append(f"${stage.cost_usd:.3f}")
    details: list[str] = []
    if stage.model:
        details.append(stage.model)
    if stage.duration_s:
        details.append(format_stage_duration(stage.duration_s))
    if stage.turns > 1:
        details.append(f"{stage.turns} turns")
    if details:
        parts.append(f"({', '.join(details)})")
    return " ".join(parts)


def stage_breakdown_line(stages: list[StageSummary]) -> str:
    if not stages:
        return ""
    return "  " + " · ".join(format_stage_line(s) for s in stages)


class RunScreen(Screen):
    BINDINGS = [
        ("ctrl+c", "cancel", "Cancel"),
        ("q", "quit_or_return", "Quit / back"),
    ]

    def __init__(
        self,
        workflow: Workflow,
        ctx: ExecutionContext,
        *,
        loop: bool = False,
        max_iterations: int | None = None,
    ) -> None:
        super().__init__()
        self._workflow = workflow
        self._base_ctx = ctx
        self._loop = loop
        # Single-run is implemented as loop with max_iterations=1
        self._max_iterations = max_iterations if loop else 1
        self._state: Literal["running", "completed", "failed", "cancelled"] = "running"
        self._cumulative_cost = 0.0
        self._loop_state = LoopState()
        self._started_at = 0.0
        self._content: Widget | None = None
        self._footer_widget: Static | None = None
        self._worker: Worker[None] | None = None
        self._sink: StageCountingSink | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        assert self._workflow.content_widget_cls is not None, (
            f"{type(self._workflow).__name__}.content_widget_cls must not be None"
        )
        self._content = self._workflow.content_widget_cls()
        yield self._content
        footer = Static(self._footer_text(), id="run-footer")
        self._footer_widget = footer
        yield footer
        yield Footer()

    def on_mount(self) -> None:
        self._started_at = time.monotonic()
        self._sink = StageCountingSink(self._content, on_cost=self._add_cost)
        self._base_ctx.event_sink = self._sink
        if hasattr(self._content, "prompt"):
            self._base_ctx.input_provider = self._content
        self.set_interval(1.0, self._update_clock)
        self._worker = self.run_worker(self._run_loop(), exclusive=True)

    def _elapsed(self) -> str:
        if self._started_at == 0.0:
            return "0s"
        secs = int(time.monotonic() - self._started_at)
        if secs < 60:
            return f"{secs}s"
        return f"{secs // 60}m{secs % 60:02d}s"

    def _footer_text(self) -> str:
        iteration = self._loop_state.iteration
        max_iter = self._max_iterations
        if self._loop and max_iter is not None:
            counter = f"iteration {iteration}/{max_iter}"
        elif self._loop:
            counter = f"iteration {iteration}"
        else:
            counter = f"state={self._state}"
        return f"{counter}  cost=${self._cumulative_cost:.3f}  elapsed={self._elapsed()}"

    def _add_cost(self, cost: float) -> None:
        self._cumulative_cost += cost
        self._refresh_footer()

    def _refresh_footer(self) -> None:
        if self._footer_widget is not None:
            self._footer_widget.update(self._footer_text())

    def _update_clock(self) -> None:
        self._refresh_footer()

    async def _run_loop(self) -> None:
        try:
            while True:
                if (
                    self._max_iterations is not None
                    and self._loop_state.iteration >= self._max_iterations
                ):
                    break
                self._loop_state.iteration += 1
                self._announce_iteration_divider()
                ctx = fresh_iteration_context(
                    self._base_ctx,
                    preserve_item=self._loop_state.iteration == 1
                    and self._base_ctx.item is not None,
                )
                results = await StageExecutor().run(self._workflow, ctx)
                if not results:
                    self._loop_state.iteration -= 1  # empty-queue probe: don't count
                    break
                self._refresh_footer()
        except asyncio.CancelledError:
            self._state = "cancelled"
            self._show_cancellation_panel()
            raise
        except Exception as e:
            self._state = "failed"
            self._show_error_panel(e)
            return
        self._state = "completed"
        self._show_loop_summary_panel()
        self._refresh_footer()

    def _announce_iteration_divider(self) -> None:
        if not self._loop or self._loop_state.iteration <= 1:
            return
        if not self.is_attached:
            return
        self.mount(
            Static(
                f"═══ iteration {self._loop_state.iteration} ═══",
                classes="iteration-divider",
            ),
            before=self._footer_widget,
        )

    def _set_result_panel(self, markup: str) -> None:
        """Mount or update the result panel."""
        if not self.is_attached:
            return
        panels = self.query("#result-panel")
        if panels:
            panels.first(Static).update(markup)
        else:
            self.mount(Static(markup, id="result-panel"))

    def _stage_breakdown_line(self) -> str:
        if self._sink is None:
            return ""
        return stage_breakdown_line(self._sink.stages)

    def _show_loop_summary_panel(self) -> None:
        iterations = self._loop_state.iteration
        cost = self._cumulative_cost
        total_elapsed = self._elapsed()
        if self._loop:
            header = (
                f"[green]✓ Complete[/green] — {iterations} iteration(s), "
                f"${cost:.3f} · {total_elapsed} total"
            )
        else:
            header = f"[green]✓ Complete[/green] — ${cost:.3f} · {total_elapsed} total"
        breakdown = self._stage_breakdown_line()
        self._set_result_panel(f"{header}\n{breakdown}" if breakdown else header)

    def _show_error_panel(self, e: Exception) -> None:
        if self._sink is not None:
            self._sink.mark_running_stages_failed()
        header = f"[red]✗ Failed:[/red] {e!s}"
        breakdown = self._stage_breakdown_line()
        self._set_result_panel(f"{header}\n{breakdown}" if breakdown else header)

    def _show_cancellation_panel(self) -> None:
        self._set_result_panel("[yellow]Cancelled[/yellow]")

    def action_cancel(self) -> None:
        if self._state == "running" and self._worker is not None:
            self._worker.cancel()

    def action_quit_or_return(self) -> None:
        if self._state == "running":
            return
        self.app.pop_screen()

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        # Hide `q Quit / back` from the footer while a text input is focused —
        # `q` is a printable char there and the binding won't fire. Ctrl+C still
        # cancels.
        if action == "quit_or_return":
            from textual.widgets import TextArea

            if isinstance(self.focused, TextArea):
                return None
        return True
