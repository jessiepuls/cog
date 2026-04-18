"""RunScreen — single-run lifecycle. Loop semantics deferred to #16."""

import asyncio
import time
from typing import Literal

from textual.app import ComposeResult
from textual.screen import Screen
from textual.widget import Widget
from textual.widgets import Footer, Header, Static
from textual.worker import Worker

from cog.core.context import ExecutionContext
from cog.core.outcomes import StageResult
from cog.core.runner import RunEvent, StageEndEvent
from cog.core.workflow import StageExecutor, Workflow


class _CountingSink:
    """Wraps the content widget sink, forwarding events and accumulating cost."""

    def __init__(self, inner: object, screen: "RunScreen") -> None:
        self._inner = inner
        self._screen = screen

    async def emit(self, event: RunEvent) -> None:
        if hasattr(self._inner, "emit"):
            await self._inner.emit(event)
        if isinstance(event, StageEndEvent):
            self._screen._cumulative_cost += event.cost_usd
            self._screen._refresh_footer()


class RunScreen(Screen):
    BINDINGS = [
        ("ctrl+c", "cancel", "Cancel"),
        ("q", "quit_or_return", "Quit / back"),
    ]

    def __init__(self, workflow: Workflow, ctx: ExecutionContext) -> None:
        super().__init__()
        self._workflow = workflow
        self._ctx = ctx
        self._state: Literal["running", "completed", "failed", "cancelled"] = "running"
        self._cumulative_cost = 0.0
        self._started_at = 0.0
        self._content: Widget | None = None
        self._footer_widget: Static | None = None
        self._worker: Worker[None] | None = None

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
        self._ctx.event_sink = _CountingSink(self._content, self)
        if hasattr(self._content, "prompt"):
            self._ctx.input_provider = self._content
        self.set_interval(1.0, self._update_clock)
        self._worker = self.run_worker(self._run_once(), exclusive=True)

    def _elapsed(self) -> str:
        if self._started_at == 0.0:
            return "0s"
        secs = int(time.monotonic() - self._started_at)
        if secs < 60:
            return f"{secs}s"
        return f"{secs // 60}m{secs % 60:02d}s"

    def _footer_text(self) -> str:
        return f"state={self._state}  elapsed={self._elapsed()}  cost=${self._cumulative_cost:.3f}"

    def _refresh_footer(self) -> None:
        if self._footer_widget is not None:
            self._footer_widget.update(self._footer_text())

    def _update_clock(self) -> None:
        self._refresh_footer()

    async def _run_once(self) -> None:
        try:
            results = await StageExecutor().run(self._workflow, self._ctx)
            self._state = "completed"
            self._show_completion_panel(results)
        except asyncio.CancelledError:
            self._state = "cancelled"
            self._show_cancellation_panel()
        except Exception as e:
            self._state = "failed"
            self._show_error_panel(e)
        finally:
            self._refresh_footer()

    def _set_result_panel(self, markup: str) -> None:
        """Mount or update the result panel."""
        if not self.is_attached:
            return
        panels = self.query("#result-panel")
        if panels:
            panels.first(Static).update(markup)
        else:
            self.mount(Static(markup, id="result-panel"))

    def _show_completion_panel(self, results: list[StageResult]) -> None:
        cost = sum(r.cost_usd for r in results)
        self._set_result_panel(f"[green]✓ Complete[/green] — ${cost:.3f}")

    def _show_error_panel(self, e: Exception) -> None:
        self._set_result_panel(f"[red]✗ Failed:[/red] {e!s}")

    def _show_cancellation_panel(self) -> None:
        self._set_result_panel("[yellow]Cancelled[/yellow]")

    def action_cancel(self) -> None:
        if self._state == "running" and self._worker is not None:
            self._worker.cancel()

    def action_quit_or_return(self) -> None:
        if self._state == "running":
            return
        self.app.pop_screen()
