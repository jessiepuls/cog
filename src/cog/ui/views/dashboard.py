"""DashboardView — ambient project + workflow status (#121, #123).

Replaces the stub Dashboard view in the shell. Shows queue counts per
workflow, a recent-runs strip (sparkline + outcome bar + last-N rows),
project status (branch + working tree), and cost totals.

Ambient information only — no click-to-launch. Workflow launch lives in
the Refine and Ralph views (#124, #125).
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import Static

import cog.git as git
from cog.core.errors import GitError
from cog.core.tracker import IssueTracker
from cog.state_paths import project_state_dir
from cog.ui.widgets.recent_runs import RecentRunsWidget
from cog.workflows import WORKFLOWS


class DashboardView(Widget):
    """Ambient dashboard shown as the shell's default-active view."""

    DEFAULT_CSS = """
    DashboardView {
        height: 1fr;
        layout: vertical;
    }
    DashboardView #dashboard-status {
        height: auto;
        padding: 0 1;
        border: solid $primary;
        margin-bottom: 1;
    }
    DashboardView #dashboard-status-title {
        text-style: bold;
        color: $text-muted;
    }
    DashboardView #dashboard-queues {
        height: auto;
        padding: 0 1;
        border: solid $primary;
        margin-bottom: 1;
    }
    DashboardView #dashboard-queues-title {
        text-style: bold;
        color: $text-muted;
    }
    DashboardView RecentRunsWidget {
        margin-bottom: 1;
    }
    """

    def __init__(self, project_dir: Path, tracker: IssueTracker) -> None:
        super().__init__(id="view-dashboard")
        self._project_dir = project_dir
        self._tracker = tracker

    def compose(self) -> ComposeResult:
        yield Static("Project", id="dashboard-status-title")
        yield Static("", id="dashboard-status")
        yield Static("", id="dashboard-cost")
        yield Static("Queues", id="dashboard-queues-title")
        yield Static("", id="dashboard-queues")
        yield RecentRunsWidget(self._project_dir)

    async def on_mount(self) -> None:
        await self.refresh_all()

    async def on_show(self) -> None:
        # Textual fires Show when display toggles from False → True — i.e.
        # when the user switches back to the dashboard tab. Refresh so the
        # queue counts, cost totals, and recent runs reflect the latest
        # state (workflow runs that happened while the dashboard was hidden).
        await self.refresh_all()

    async def refresh_all(self) -> None:
        await asyncio.gather(
            self._refresh_project_status(),
            self._refresh_queue_counts(),
            self._refresh_cost_totals(),
            self._refresh_recent_runs(),
        )

    async def _refresh_recent_runs(self) -> None:
        try:
            widget = self.query_one(RecentRunsWidget)
        except Exception:  # noqa: BLE001 — may not be mounted yet
            return
        await widget.refresh_runs()

    async def _refresh_project_status(self) -> None:
        try:
            branch = await git.current_branch(self._project_dir)
        except GitError:
            branch = "(detached)"
        try:
            status = await git.working_tree_status(self._project_dir)
            if status.is_clean:
                tree_line = "tree: [green]clean[/green]"
            else:
                parts = []
                if status.changed:
                    parts.append(f"{status.changed} changed")
                if status.untracked:
                    parts.append(f"{status.untracked} untracked")
                tree_line = f"tree: [yellow]{', '.join(parts)}[/yellow]"
        except GitError:
            tree_line = "tree: [dim](not a git repo)[/dim]"

        widget = self.query_one("#dashboard-status", Static)
        widget.update(f"branch: [bold]{branch}[/bold]  ·  {tree_line}")

    async def _refresh_queue_counts(self) -> None:
        results = await asyncio.gather(
            *(self._safe_count(w.queue_label) for w in WORKFLOWS),
            return_exceptions=True,
        )
        lines: list[str] = []
        for cls, count in zip(WORKFLOWS, results, strict=False):
            if isinstance(count, BaseException):
                lines.append(f"  [red]?[/red] {cls.name}: error reading {cls.queue_label}")
            else:
                lines.append(f"  [bold]{count}[/bold] {cls.queue_label}  ([dim]{cls.name}[/dim])")
        widget = self.query_one("#dashboard-queues", Static)
        widget.update("\n".join(lines))

    async def _safe_count(self, label: str) -> int:
        items = await self._tracker.list_by_label(label, assignee="@me")
        return len(items)

    async def _refresh_cost_totals(self) -> None:
        today, week, all_time, by_workflow = self._compute_cost_totals()
        widget = self.query_one("#dashboard-cost", Static)
        totals_line = (
            f"cost: [bold]${today:.2f}[/bold] today  ·  "
            f"[bold]${week:.2f}[/bold] last 7d  ·  "
            f"[bold]${all_time:.2f}[/bold] all time"
        )
        if by_workflow:
            breakdown = "  ·  ".join(
                f"{name} [bold]${cost:.2f}[/bold]"
                for name, cost in sorted(by_workflow.items(), key=lambda x: x[1], reverse=True)
            )
            widget.update(f"{totals_line}\n[dim]by workflow:[/dim]  {breakdown}")
        else:
            widget.update(totals_line)

    def _compute_cost_totals(self) -> tuple[float, float, float, dict[str, float]]:
        path = project_state_dir(self._project_dir) / "runs.jsonl"
        if not path.exists():
            return 0.0, 0.0, 0.0, {}
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            return 0.0, 0.0, 0.0, {}

        now = datetime.now(UTC)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        week_start = now - timedelta(days=7)

        today_total = 0.0
        week_total = 0.0
        all_total = 0.0
        by_workflow: dict[str, float] = {}

        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(rec, dict):
                continue
            cost = float(rec.get("total_cost_usd", 0.0) or 0.0)
            workflow = rec.get("workflow", "?")
            all_total += cost
            if isinstance(workflow, str):
                by_workflow[workflow] = by_workflow.get(workflow, 0.0) + cost
            ts_raw = rec.get("ts", "")
            try:
                ts = datetime.fromisoformat(ts_raw)
            except (TypeError, ValueError):
                continue
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=UTC)
            if ts >= week_start:
                week_total += cost
            if ts >= today_start:
                today_total += cost

        return today_total, week_total, all_total, by_workflow
