"""Tests for RecentRunsWidget."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Sparkline, Static

from cog.state_paths import project_state_dir
from cog.ui.widgets.recent_runs import RecentRunsWidget


@pytest.fixture
def xdg_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setenv("XDG_STATE_HOME", str(state))
    return state


def _runs_path(project_dir: Path) -> Path:
    return project_state_dir(project_dir) / "runs.jsonl"


def _write_runs(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


def _record(
    *,
    ts: datetime | None = None,
    workflow: str = "ralph",
    item: int | None = 1,
    outcome: str = "success",
    cost: float = 0.5,
    duration: float = 120.0,
) -> dict:
    if ts is None:
        ts = datetime.now(UTC)
    return {
        "ts": ts.isoformat(),
        "workflow": workflow,
        "item": item,
        "outcome": outcome,
        "total_cost_usd": cost,
        "duration_seconds": duration,
    }


class _RecentApp(App):
    def __init__(self, project_dir: Path) -> None:
        super().__init__()
        self._project_dir = project_dir

    def compose(self) -> ComposeResult:
        yield RecentRunsWidget(self._project_dir)


async def test_recent_runs_widget_reads_jsonl_and_renders_rows(
    tmp_path: Path, xdg_state: Path
) -> None:
    _write_runs(
        _runs_path(tmp_path),
        [
            _record(workflow="ralph", item=42, outcome="success", cost=0.47),
            _record(workflow="refine", item=107, outcome="no-op", cost=0.03),
        ],
    )
    async with _RecentApp(tmp_path).run_test(headless=True) as pilot:
        await pilot.pause()
        rows = pilot.app.query_one("#recent-rows", Static)
        rendered = str(rows.renderable)
        assert "ralph" in rendered
        assert "#42" in rendered
        assert "success" in rendered
        assert "refine" in rendered
        assert "no-op" in rendered


async def test_recent_runs_widget_handles_missing_file_gracefully(
    tmp_path: Path, xdg_state: Path
) -> None:
    async with _RecentApp(tmp_path).run_test(headless=True) as pilot:
        await pilot.pause()
        empty = pilot.app.query_one("#recent-empty", Static)
        assert "no runs yet" in str(empty.renderable)


async def test_recent_runs_widget_skips_malformed_lines(tmp_path: Path, xdg_state: Path) -> None:
    runs = _runs_path(tmp_path)
    runs.parent.mkdir(parents=True, exist_ok=True)
    with runs.open("w") as f:
        f.write(json.dumps(_record(item=1)) + "\n")
        f.write("not json at all\n")
        f.write("{broken json\n")
        f.write(json.dumps(_record(item=2)) + "\n")

    async with _RecentApp(tmp_path).run_test(headless=True) as pilot:
        await pilot.pause()
        rows = pilot.app.query_one("#recent-rows", Static)
        rendered = str(rows.renderable)
        assert "#1" in rendered
        assert "#2" in rendered


async def test_recent_runs_widget_populates_sparkline_from_costs(
    tmp_path: Path, xdg_state: Path
) -> None:
    _write_runs(
        _runs_path(tmp_path),
        [
            _record(cost=0.10, item=1),
            _record(cost=0.25, item=2),
            _record(cost=0.05, item=3),
        ],
    )
    async with _RecentApp(tmp_path).run_test(headless=True) as pilot:
        await pilot.pause()
        sparkline = pilot.app.query_one("#recent-cost-sparkline", Sparkline)
        assert list(sparkline.data) == [0.10, 0.25, 0.05]


async def test_recent_runs_widget_aggregates_outcome_counts(
    tmp_path: Path, xdg_state: Path
) -> None:
    _write_runs(
        _runs_path(tmp_path),
        [
            _record(outcome="success", item=1),
            _record(outcome="success", item=2),
            _record(outcome="success", item=3),
            _record(outcome="no-op", item=4),
            _record(outcome="error", item=5),
        ],
    )
    async with _RecentApp(tmp_path).run_test(headless=True) as pilot:
        await pilot.pause()
        bar = pilot.app.query_one("#recent-outcome-bar", Static)
        rendered = str(bar.renderable)
        assert "success 3" in rendered
        assert "no-op 1" in rendered
        assert "error 1" in rendered


async def test_recent_runs_widget_empty_state_when_no_records(
    tmp_path: Path, xdg_state: Path
) -> None:
    runs = _runs_path(tmp_path)
    runs.parent.mkdir(parents=True, exist_ok=True)
    runs.write_text("")  # empty file

    async with _RecentApp(tmp_path).run_test(headless=True) as pilot:
        await pilot.pause()
        empty = pilot.app.query_one("#recent-empty", Static)
        assert "no runs yet" in str(empty.renderable)
        rows = pilot.app.query_one("#recent-rows", Static)
        assert rows.display is False


async def test_recent_runs_widget_filters_chat_from_row_list(
    tmp_path: Path, xdg_state: Path
) -> None:
    # Chat turns would otherwise dominate the 5-row window. Verify they're
    # filtered from rows + outcome bar but still counted in the sparkline.
    _write_runs(
        _runs_path(tmp_path),
        [
            _record(workflow="ralph", item=1, outcome="success", cost=0.20),
            _record(workflow="chat", item=None, outcome="success", cost=0.05),
            _record(workflow="chat", item=None, outcome="success", cost=0.05),
            _record(workflow="chat", item=None, outcome="success", cost=0.05),
            _record(workflow="refine", item=2, outcome="success", cost=0.10),
        ],
    )
    async with _RecentApp(tmp_path).run_test(headless=True) as pilot:
        await pilot.pause()
        rows = pilot.app.query_one("#recent-rows", Static)
        rendered = str(rows.renderable)
        # ralph + refine shown, chat not
        assert "ralph" in rendered
        assert "refine" in rendered
        assert "chat" not in rendered

        # Sparkline still has all 5 costs
        sparkline = pilot.app.query_one("#recent-cost-sparkline", Sparkline)
        assert len(list(sparkline.data)) == 5


async def test_recent_runs_widget_shows_empty_state_when_only_chat(
    tmp_path: Path, xdg_state: Path
) -> None:
    _write_runs(
        _runs_path(tmp_path),
        [
            _record(workflow="chat", item=None, outcome="success", cost=0.03),
            _record(workflow="chat", item=None, outcome="success", cost=0.04),
        ],
    )
    async with _RecentApp(tmp_path).run_test(headless=True) as pilot:
        await pilot.pause()
        rows = pilot.app.query_one("#recent-rows", Static)
        assert "no workflow runs" in str(rows.renderable)


async def test_recent_runs_widget_humanizes_timestamps(tmp_path: Path, xdg_state: Path) -> None:
    now = datetime.now(UTC)
    _write_runs(
        _runs_path(tmp_path),
        [
            _record(ts=now - timedelta(minutes=30), item=1),
            _record(ts=now - timedelta(seconds=10), item=2),
        ],
    )

    async with _RecentApp(tmp_path).run_test(headless=True) as pilot:
        await pilot.pause()
        rows = pilot.app.query_one("#recent-rows", Static)
        rendered = str(rows.renderable)
        # Most-recent first; the 10s-ago row is on top
        assert "10s ago" in rendered
        assert "30m ago" in rendered
