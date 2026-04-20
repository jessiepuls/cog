"""Tests for DashboardView (#123)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Static

from cog.core.errors import GitError
from cog.core.tracker import IssueTracker
from cog.state_paths import project_state_dir
from cog.ui.views.dashboard import DashboardView


@pytest.fixture
def xdg_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setenv("XDG_STATE_HOME", str(state))
    return state


def _tracker_with_counts(**label_counts: int) -> IssueTracker:
    t = AsyncMock(spec=IssueTracker)

    async def list_by_label(label: str, *, assignee: str | None = None):
        count = label_counts.get(label, 0)
        return [object()] * count  # just needs len()

    t.list_by_label = AsyncMock(side_effect=list_by_label)
    return t  # type: ignore[return-value]


class _DashApp(App):
    def __init__(self, project_dir: Path, tracker: IssueTracker) -> None:
        super().__init__()
        self._project_dir = project_dir
        self._tracker = tracker

    def compose(self) -> ComposeResult:
        yield DashboardView(self._project_dir, self._tracker)


async def test_dashboard_view_renders_queue_counts_per_workflow(
    tmp_path: Path, xdg_state: Path
) -> None:
    tracker = _tracker_with_counts(**{"agent-ready": 3, "needs-refinement": 7})
    async with _DashApp(tmp_path, tracker).run_test(headless=True) as pilot:
        await pilot.pause()
        queues = pilot.app.query_one("#dashboard-queues", Static)
        rendered = str(queues.renderable)
        assert "3" in rendered
        assert "agent-ready" in rendered
        assert "7" in rendered
        assert "needs-refinement" in rendered


async def test_dashboard_view_renders_recent_runs_strip(tmp_path: Path, xdg_state: Path) -> None:
    # Write a run so the recent-runs widget has something to show.
    runs = project_state_dir(tmp_path) / "runs.jsonl"
    runs.parent.mkdir(parents=True, exist_ok=True)
    runs.write_text(
        json.dumps(
            {
                "ts": datetime.now(UTC).isoformat(),
                "workflow": "ralph",
                "item": 42,
                "outcome": "success",
                "total_cost_usd": 0.47,
                "duration_seconds": 480,
            }
        )
        + "\n"
    )
    tracker = _tracker_with_counts()

    async with _DashApp(tmp_path, tracker).run_test(headless=True) as pilot:
        await pilot.pause()
        # Recent-runs widget is mounted; its internal labels get populated.
        rows = pilot.app.query_one("#recent-rows", Static)
        assert "ralph" in str(rows.renderable)


async def test_dashboard_view_renders_cost_totals(tmp_path: Path, xdg_state: Path) -> None:
    runs = project_state_dir(tmp_path) / "runs.jsonl"
    runs.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC)
    records = [
        {
            "ts": now.isoformat(),
            "total_cost_usd": 0.25,
            "item": 1,
            "outcome": "success",
            "workflow": "ralph",
            "duration_seconds": 60,
        },
        {
            "ts": (now - timedelta(days=3)).isoformat(),
            "total_cost_usd": 0.50,
            "item": 2,
            "outcome": "success",
            "workflow": "ralph",
            "duration_seconds": 60,
        },
        {
            "ts": (now - timedelta(days=30)).isoformat(),
            "total_cost_usd": 1.00,
            "item": 3,
            "outcome": "success",
            "workflow": "ralph",
            "duration_seconds": 60,
        },
    ]
    with runs.open("w") as f:
        for rec in records:
            f.write(json.dumps(rec) + "\n")

    tracker = _tracker_with_counts()
    async with _DashApp(tmp_path, tracker).run_test(headless=True) as pilot:
        await pilot.pause()
        cost = pilot.app.query_one("#dashboard-cost", Static)
        rendered = str(cost.renderable)
        # today: $0.25, week: $0.75 (0.25 + 0.50), all: $1.75
        assert "$0.25" in rendered
        assert "$0.75" in rendered
        assert "$1.75" in rendered


async def test_dashboard_view_renders_project_status_line(tmp_path: Path, xdg_state: Path) -> None:
    tracker = _tracker_with_counts()

    with (
        patch("cog.git.current_branch", new=AsyncMock(return_value="feature/xyz")),
        patch(
            "cog.git.working_tree_status",
            new=AsyncMock(
                return_value=__import__(
                    "cog.git", fromlist=["WorkingTreeStatus"]
                ).WorkingTreeStatus(changed=2, untracked=1)
            ),
        ),
    ):
        async with _DashApp(tmp_path, tracker).run_test(headless=True) as pilot:
            await pilot.pause()
            status = pilot.app.query_one("#dashboard-status", Static)
            rendered = str(status.renderable)
            assert "feature/xyz" in rendered
            assert "2 changed" in rendered
            assert "1 untracked" in rendered


async def test_dashboard_view_status_shows_clean_when_tree_is_clean(
    tmp_path: Path, xdg_state: Path
) -> None:
    from cog.git import WorkingTreeStatus

    tracker = _tracker_with_counts()
    with (
        patch("cog.git.current_branch", new=AsyncMock(return_value="main")),
        patch(
            "cog.git.working_tree_status",
            new=AsyncMock(return_value=WorkingTreeStatus(changed=0, untracked=0)),
        ),
    ):
        async with _DashApp(tmp_path, tracker).run_test(headless=True) as pilot:
            await pilot.pause()
            status = pilot.app.query_one("#dashboard-status", Static)
            assert "clean" in str(status.renderable)


async def test_dashboard_view_status_handles_detached_head_gracefully(
    tmp_path: Path, xdg_state: Path
) -> None:
    from cog.git import WorkingTreeStatus

    tracker = _tracker_with_counts()
    with (
        patch("cog.git.current_branch", new=AsyncMock(side_effect=GitError("detached"))),
        patch(
            "cog.git.working_tree_status",
            new=AsyncMock(return_value=WorkingTreeStatus(changed=0, untracked=0)),
        ),
    ):
        async with _DashApp(tmp_path, tracker).run_test(headless=True) as pilot:
            await pilot.pause()
            status = pilot.app.query_one("#dashboard-status", Static)
            assert "detached" in str(status.renderable)


async def test_dashboard_view_status_handles_non_git_directory(
    tmp_path: Path, xdg_state: Path
) -> None:
    tracker = _tracker_with_counts()
    with (
        patch("cog.git.current_branch", new=AsyncMock(side_effect=GitError("not a repo"))),
        patch(
            "cog.git.working_tree_status",
            new=AsyncMock(side_effect=GitError("not a repo")),
        ),
    ):
        async with _DashApp(tmp_path, tracker).run_test(headless=True) as pilot:
            await pilot.pause()
            status = pilot.app.query_one("#dashboard-status", Static)
            assert "not a git repo" in str(status.renderable)


async def test_dashboard_view_cost_totals_empty_when_no_runs(
    tmp_path: Path, xdg_state: Path
) -> None:
    tracker = _tracker_with_counts()
    async with _DashApp(tmp_path, tracker).run_test(headless=True) as pilot:
        await pilot.pause()
        cost = pilot.app.query_one("#dashboard-cost", Static)
        rendered = str(cost.renderable)
        assert "$0.00" in rendered


async def test_dashboard_view_refresh_all_is_idempotent(tmp_path: Path, xdg_state: Path) -> None:
    tracker = _tracker_with_counts(**{"agent-ready": 5})
    async with _DashApp(tmp_path, tracker).run_test(headless=True) as pilot:
        await pilot.pause()
        view = pilot.app.query_one(DashboardView)
        await view.refresh_all()
        await pilot.pause()
        queues = pilot.app.query_one("#dashboard-queues", Static)
        assert "5" in str(queues.renderable)
