"""RecentRunsWidget — ambient telemetry display for the main menu."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widget import Widget
from textual.widgets import Sparkline, Static

from cog.state_paths import project_state_dir

_RECENT_ROWS = 5
_SPARKLINE_WINDOW = 30

_OUTCOME_STYLES: dict[str, str] = {
    "success": "green",
    "no-op": "dim",
    "error": "red",
    "push-failed": "red",
    "rebase-conflict": "red",
    "ci-failed": "red",
    "deferred-by-blocker": "yellow",
}


def _tail_jsonl(path: Path, limit: int) -> list[dict]:
    """Return up to `limit` most-recent valid JSON records from a JSONL file.

    Missing file → []. Malformed lines are skipped.
    """
    if not path.exists():
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    records: list[dict] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            records.append(obj)
    return records[-limit:]


def _humanize_ago(ts_iso: str, now: datetime) -> str:
    try:
        ts = datetime.fromisoformat(ts_iso)
    except ValueError:
        return "?"
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    delta = now - ts
    secs = int(delta.total_seconds())
    if secs < 60:
        return f"{secs}s ago"
    if secs < 3600:
        return f"{secs // 60}m ago"
    if secs < 86400:
        return f"{secs // 3600}h ago"
    return f"{secs // 86400}d ago"


def _format_duration(seconds: float) -> str:
    secs = int(seconds)
    if secs < 60:
        return f"{secs}s"
    return f"{secs // 60}m{secs % 60:02d}s"


class RecentRunsWidget(Widget):
    """Ambient telemetry strip: last-N runs list, cost sparkline, outcome bar."""

    DEFAULT_CSS = """
    RecentRunsWidget {
        height: auto;
        padding: 1;
        border: solid $primary;
    }
    RecentRunsWidget > #recent-title {
        color: $text-muted;
        text-style: bold;
        height: 1;
    }
    RecentRunsWidget > #recent-rows {
        height: auto;
    }
    RecentRunsWidget > #recent-sparkline-row {
        height: 2;
    }
    RecentRunsWidget > #recent-sparkline-row > #recent-sparkline-label {
        width: auto;
        color: $text-muted;
    }
    RecentRunsWidget > #recent-sparkline-row > Sparkline {
        width: 1fr;
        height: 2;
    }
    RecentRunsWidget > #recent-outcome-bar {
        height: 1;
    }
    RecentRunsWidget > #recent-empty {
        color: $text-muted;
        height: 1;
    }
    """

    def __init__(self, project_dir: Path) -> None:
        super().__init__()
        self._project_dir = project_dir

    def compose(self) -> ComposeResult:
        yield Static("Recent runs", id="recent-title")
        yield Static("", id="recent-empty")
        yield Static("", id="recent-rows")
        with Horizontal(id="recent-sparkline-row"):
            yield Static("cost: ", id="recent-sparkline-label")
            yield Sparkline([], id="recent-cost-sparkline")
        yield Static("", id="recent-outcome-bar")

    async def on_mount(self) -> None:
        await self.refresh_runs()

    async def on_screen_resume(self) -> None:
        await self.refresh_runs()

    async def refresh_runs(self) -> None:
        path = project_state_dir(self._project_dir) / "runs.jsonl"
        records = _tail_jsonl(path, _SPARKLINE_WINDOW)

        empty_label = self.query_one("#recent-empty", Static)
        rows_label = self.query_one("#recent-rows", Static)
        sparkline_row = self.query_one("#recent-sparkline-row")
        sparkline = self.query_one("#recent-cost-sparkline", Sparkline)
        outcome_bar = self.query_one("#recent-outcome-bar", Static)

        if not records:
            empty_label.update("no runs yet")
            rows_label.display = False
            sparkline_row.display = False
            outcome_bar.display = False
            return

        empty_label.display = False
        rows_label.display = True
        sparkline_row.display = True
        outcome_bar.display = True

        now = datetime.now(UTC)
        rows = _render_rows(records[-_RECENT_ROWS:][::-1], now=now)
        rows_label.update(rows)

        costs = [float(r.get("total_cost_usd", 0.0) or 0.0) for r in records]
        sparkline.data = costs

        outcome_bar.update(_render_outcome_bar(records))


def _render_rows(records: list[dict], *, now: datetime) -> Text:
    lines: list[Text] = []
    for r in records:
        ago = _humanize_ago(r.get("ts", ""), now)
        workflow = r.get("workflow", "?")
        item = r.get("item")
        item_str = f"#{item}" if item is not None else ""
        outcome = r.get("outcome", "?")
        style = _OUTCOME_STYLES.get(outcome, "white")
        cost = float(r.get("total_cost_usd", 0.0) or 0.0)
        duration = _format_duration(float(r.get("duration_seconds", 0.0) or 0.0))

        line = Text()
        line.append(f"{ago:<10}", style="dim")
        line.append(f"  {workflow} {item_str}".ljust(20))
        line.append(f"→ {outcome}".ljust(22), style=style)
        line.append(f"  (${cost:.3f}, {duration})", style="dim")
        lines.append(line)
    result = Text()
    for i, line in enumerate(lines):
        if i > 0:
            result.append("\n")
        result.append(line)
    return result


def _render_outcome_bar(records: list[dict]) -> Text:
    counts: dict[str, int] = {}
    for r in records:
        o = r.get("outcome", "?")
        counts[o] = counts.get(o, 0) + 1

    priority = (
        "success",
        "no-op",
        "error",
        "ci-failed",
        "push-failed",
        "rebase-conflict",
        "deferred-by-blocker",
    )
    ordered = [o for o in priority if o in counts] + [o for o in counts if o not in priority]

    out = Text()
    for i, outcome in enumerate(ordered):
        if i > 0:
            out.append(" · ", style="dim")
        style = _OUTCOME_STYLES.get(outcome, "white")
        out.append(f"{outcome} {counts[outcome]}", style=style)
    return out
