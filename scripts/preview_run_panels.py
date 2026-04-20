"""Eyeball the RunScreen completion / failure panels without running a real
workflow.

Run: `uv run python scripts/preview_run_panels.py`

Displays a fake RunScreen with three stages' worth of data, already pre-
populated, and shows both the success panel and the failure panel in
succession. Press `q` to advance to the next panel; press `q` on the
last panel to exit.
"""

from __future__ import annotations

from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Footer, Header, Static

from cog.ui.screens.run import RunScreen, _StageSummary


def _success_stages() -> list[_StageSummary]:
    return [
        _StageSummary(
            name="build",
            model="claude-sonnet-4-6",
            cost_usd=0.20,
            duration_s=180,
            turns=1,
            status="completed",
        ),
        _StageSummary(
            name="review",
            model="claude-opus-4-7",
            cost_usd=0.18,
            duration_s=120,
            turns=1,
            status="completed",
        ),
        _StageSummary(
            name="document",
            model="claude-sonnet-4-6",
            cost_usd=0.05,
            duration_s=60,
            turns=1,
            status="completed",
        ),
    ]


def _failure_stages() -> list[_StageSummary]:
    return [
        _StageSummary(
            name="build",
            model="claude-sonnet-4-6",
            cost_usd=0.02,
            duration_s=120,
            turns=1,
            status="failed",
        ),
    ]


def _interview_stages() -> list[_StageSummary]:
    return [
        _StageSummary(name="interview", cost_usd=0.08, turns=3, status="completed"),
        _StageSummary(
            name="rewrite",
            model="claude-opus-4-7",
            cost_usd=0.04,
            duration_s=60,
            turns=1,
            status="completed",
        ),
    ]


class _PreviewScreen(Screen):
    BINDINGS = [
        Binding("q", "next", "Next / quit"),
    ]

    DEFAULT_CSS = """
    #preview-title {
        height: 1;
        text-style: bold;
        padding: 0 1;
    }
    #result-panel {
        height: auto;
        padding: 1;
        border: solid $primary;
        margin: 1 2;
    }
    """

    _panels = [
        ("Ralph success — build → review → document", _success_stages, False, 360, 3),
        ("Ralph failure — build stage stalled", _failure_stages, True, 120, 1),
        ("Refine success — interview (3 turns) → rewrite", _interview_stages, False, 240, 1),
    ]

    def __init__(self, idx: int = 0) -> None:
        super().__init__()
        self._idx = idx

    def compose(self) -> ComposeResult:
        title, stages_fn, failed, elapsed_s, iters = self._panels[self._idx]
        yield Header()
        yield Static(f"[{self._idx + 1}/{len(self._panels)}] {title}", id="preview-title")

        # Build a minimal "screen" stand-in that the panel formatters need
        class _Sink:
            stages = stages_fn()

        class _FakeScreen:
            _cumulative_cost = sum(s.cost_usd for s in _Sink.stages)
            _started_at = 0.0
            _loop = iters > 1
            _loop_state = type("L", (), {"iteration": iters})()
            _sink = _Sink

            _format_duration = RunScreen._format_duration
            _format_stage_line = RunScreen._format_stage_line
            _stage_breakdown_line = RunScreen._stage_breakdown_line

            def _elapsed(self) -> str:
                return f"{elapsed_s // 60}m{elapsed_s % 60:02d}s"

        fs = _FakeScreen()
        if failed:
            header = "[red]✗ Failed:[/red] stage 'build' failed | cause=RunnerStalledError"
        elif fs._loop:
            header = (
                f"[green]✓ Complete[/green] — {iters} iteration(s), "
                f"${fs._cumulative_cost:.3f} · {fs._elapsed()} total"
            )
        else:
            header = (
                f"[green]✓ Complete[/green] — ${fs._cumulative_cost:.3f} · {fs._elapsed()} total"
            )
        breakdown = fs._stage_breakdown_line()
        body = f"{header}\n{breakdown}" if breakdown else header
        yield Static(body, id="result-panel")
        yield Footer()

    def action_next(self) -> None:
        if self._idx + 1 < len(self._panels):
            self.app.switch_screen(_PreviewScreen(self._idx + 1))
        else:
            self.app.exit()


class _PreviewApp(App):
    def on_mount(self) -> None:
        self.push_screen(_PreviewScreen())


if __name__ == "__main__":
    # silence ruff F401 on the Path import we intentionally keep for clarity
    _ = Path
    _PreviewApp().run()
