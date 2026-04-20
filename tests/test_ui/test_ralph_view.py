"""Tests for RalphView (#125)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from textual.app import App, ComposeResult
from textual.widgets import ListView, Static

from cog.core.item import Item
from cog.core.tracker import IssueTracker
from cog.ui.views.ralph import RalphView


def _item(n: int) -> Item:
    return Item(
        tracker_id="gh",
        item_id=str(n),
        title=f"item {n}",
        body="",
        labels=("agent-ready",),
        comments=(),
        state="open",
        created_at=datetime(2026, 4, 20, tzinfo=UTC),
        updated_at=datetime(2026, 4, 20, tzinfo=UTC),
        url="",
    )


def _tracker_with(items: list[Item]) -> IssueTracker:
    t = AsyncMock(spec=IssueTracker)
    t.list_by_label = AsyncMock(return_value=items)
    return t  # type: ignore[return-value]


@pytest.fixture
def xdg_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setenv("XDG_STATE_HOME", str(state))
    return state


class _RalphApp(App):
    def __init__(self, project_dir: Path, tracker: IssueTracker) -> None:
        super().__init__()
        self._project_dir = project_dir
        self._tracker = tracker

    def compose(self) -> ComposeResult:
        yield RalphView(self._project_dir, self._tracker)


async def test_ralph_view_starts_in_idle_and_lists_queue(tmp_path: Path, xdg_state: Path) -> None:
    tracker = _tracker_with([_item(1), _item(2)])
    async with _RalphApp(tmp_path, tracker).run_test(headless=True) as pilot:
        await pilot.pause()
        view = pilot.app.query_one(RalphView)
        assert view._substate == "idle"
        queue = view.query_one("#ralph-queue", ListView)
        assert len(queue.children) == 2


async def test_ralph_view_empty_queue_shows_empty_state(tmp_path: Path, xdg_state: Path) -> None:
    tracker = _tracker_with([])
    async with _RalphApp(tmp_path, tracker).run_test(headless=True) as pilot:
        await pilot.pause()
        view = pilot.app.query_one(RalphView)
        status = view.query_one("#ralph-status", Static)
        assert "empty" in str(status.renderable).lower()


async def test_ralph_view_history_badge_rendered_for_prior_runs(
    tmp_path: Path, xdg_state: Path
) -> None:
    import json

    from cog.state_paths import project_state_dir

    # Prior run on item 1 — should surface as a badge in the picker
    runs = project_state_dir(tmp_path) / "runs.jsonl"
    runs.parent.mkdir(parents=True, exist_ok=True)
    runs.write_text(
        json.dumps(
            {
                "item": 1,
                "workflow": "ralph",
                "outcome": "success",
                "total_cost_usd": 0.35,
                "ts": datetime.now(UTC).isoformat(),
                "duration_seconds": 120,
            }
        )
        + "\n"
    )
    tracker = _tracker_with([_item(1)])
    async with _RalphApp(tmp_path, tracker).run_test(headless=True) as pilot:
        await pilot.pause()
        queue = pilot.app.query_one("#ralph-queue", ListView)
        label_text = str(queue.children[0].query_one("Label").renderable)
        assert "ralph" in label_text
        assert "×1" in label_text
        assert "success" in label_text


async def test_ralph_view_check_action_hides_cancel_outside_running(
    tmp_path: Path, xdg_state: Path
) -> None:
    tracker = _tracker_with([])
    async with _RalphApp(tmp_path, tracker).run_test(headless=True) as pilot:
        await pilot.pause()
        view = pilot.app.query_one(RalphView)
        # Idle state: cancel hidden, refresh shown
        assert view.check_action("cancel_run", ()) is None
        assert view.check_action("refresh_queue", ()) is True
        assert view.check_action("dismiss_post_run", ()) is None


async def test_ralph_view_post_run_panel_renders_breakdown(tmp_path: Path, xdg_state: Path) -> None:
    from cog.ui.screens.run import StageCountingSink, StageSummary

    tracker = _tracker_with([])
    async with _RalphApp(tmp_path, tracker).run_test(headless=True) as pilot:
        await pilot.pause()
        view = pilot.app.query_one(RalphView)

        # Prime stage data directly — no real workflow run.
        view._sink = StageCountingSink(object(), on_cost=lambda _: None)
        view._sink._stages = [
            StageSummary(
                name="build",
                model="sonnet-4-6",
                cost_usd=0.20,
                duration_s=60,
                turns=1,
                status="completed",
            ),
            StageSummary(
                name="review",
                model="opus-4-7",
                cost_usd=0.18,
                duration_s=40,
                turns=1,
                status="completed",
            ),
        ]
        view._render_post_run("[green]✓ Complete[/green] — $0.38 · 2m total")
        view._switch_to("post_run")

        panel = view.query_one("#ralph-post-run", Static)
        rendered = str(panel.renderable)
        assert "Complete" in rendered
        assert "build" in rendered
        assert "review" in rendered
        assert "$0.200" in rendered
        assert "$0.180" in rendered


async def test_ralph_view_dismiss_post_run_returns_to_idle(tmp_path: Path, xdg_state: Path) -> None:
    tracker = _tracker_with([_item(1)])
    async with _RalphApp(tmp_path, tracker).run_test(headless=True) as pilot:
        await pilot.pause()
        view = pilot.app.query_one(RalphView)
        view._render_post_run("[green]✓ Complete[/green]")
        view._switch_to("post_run")
        await pilot.pause()

        view.action_dismiss_post_run()
        for _ in range(5):
            await pilot.pause()
        assert view._substate == "idle"


async def test_ralph_view_busy_description_matches_substate(
    tmp_path: Path, xdg_state: Path
) -> None:
    tracker = _tracker_with([])
    async with _RalphApp(tmp_path, tracker).run_test(headless=True) as pilot:
        await pilot.pause()
        view = pilot.app.query_one(RalphView)
        # Idle → None
        assert view.busy_description() is None
        # Running → busy with item id
        view._substate = "running"
        view._active_item = _item(99)
        assert view.busy_description() == "Ralph run on #99"
        # Post-run → not busy (run finished)
        view._substate = "post_run"
        assert view.busy_description() is None


async def test_ralph_view_posts_attention_on_post_run_substate(
    tmp_path: Path, xdg_state: Path
) -> None:
    from cog.ui.messages import ViewAttention

    tracker = _tracker_with([])
    async with _RalphApp(tmp_path, tracker).run_test(headless=True) as pilot:
        await pilot.pause()
        view = pilot.app.query_one(RalphView)
        # Capture messages posted by the view.
        captured: list[ViewAttention] = []
        original_post = view.post_message

        def _capture(msg):
            if isinstance(msg, ViewAttention):
                captured.append(msg)
            return original_post(msg)

        view.post_message = _capture  # type: ignore[method-assign]

        view._switch_to("post_run")
        await pilot.pause()

        assert any(m.view_id == "ralph" for m in captured)


async def test_ralph_view_failure_marks_running_stages_failed(
    tmp_path: Path, xdg_state: Path
) -> None:
    from cog.ui.screens.run import StageCountingSink, StageSummary

    tracker = _tracker_with([])
    async with _RalphApp(tmp_path, tracker).run_test(headless=True) as pilot:
        await pilot.pause()
        view = pilot.app.query_one(RalphView)

        view._sink = StageCountingSink(object(), on_cost=lambda _: None)
        view._sink._stages = [
            StageSummary(name="build", cost_usd=0.0, duration_s=0, status="running"),
        ]
        view._sink._stage_starts["build"] = 0.0
        view._sink.mark_running_stages_failed()
        assert view._sink.stages[0].status == "failed"
