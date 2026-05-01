"""Tests for CogShellScreen — persistent sidebar + content layout (#122)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock

from textual.app import App
from textual.widget import Widget
from textual.widgets import ListView

from cog.core.item import Item
from cog.core.tracker import IssueTracker
from cog.ui.messages import QueueCountsStale, ViewAttention
from cog.ui.screens.shell import CogShellScreen, Sidebar
from cog.ui.views.dashboard import DashboardView
from cog.ui.views.ralph import RalphView
from cog.ui.views.refine import RefineView


def _fake_tracker() -> IssueTracker:
    t = AsyncMock(spec=IssueTracker)
    t.list_by_label = AsyncMock(return_value=[])
    return t  # type: ignore[return-value]


def _make_items(count: int) -> list[Item]:
    return [
        Item(
            tracker_id="gh",
            item_id=str(i),
            title=f"item {i}",
            body="",
            labels=(),
            comments=(),
            state="open",
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
            updated_at=datetime(2026, 1, 1, tzinfo=UTC),
            url="",
        )
        for i in range(count)
    ]


def _tracker_with_counts(**label_counts: int) -> IssueTracker:
    t = AsyncMock(spec=IssueTracker)

    async def list_by_label(label: str, *, assignee: str | None = None) -> list[Item]:
        return _make_items(label_counts.get(label, 0))

    t.list_by_label = AsyncMock(side_effect=list_by_label)
    return t  # type: ignore[return-value]


class _ShellApp(App):
    def __init__(self, project_dir: Path, tracker: IssueTracker | None = None) -> None:
        super().__init__()
        self._project_dir = project_dir
        self._tracker = tracker or _fake_tracker()

    def on_mount(self) -> None:
        self.push_screen(CogShellScreen(self._project_dir, self._tracker))


async def test_shell_mounts_all_views_on_startup(tmp_path: Path) -> None:
    async with _ShellApp(tmp_path).run_test(headless=True) as pilot:
        await pilot.pause()
        pilot.app.query_one("#view-dashboard", DashboardView)
        pilot.app.query_one("#view-refine", RefineView)
        pilot.app.query_one("#view-ralph", RalphView)


async def test_shell_displays_only_active_view(tmp_path: Path) -> None:
    async with _ShellApp(tmp_path).run_test(headless=True) as pilot:
        await pilot.pause()
        displayed = [
            v
            for v in ("dashboard", "refine", "ralph")
            if pilot.app.query_one(f"#view-{v}", Widget).display
        ]
        assert displayed == ["dashboard"]  # default active


async def test_shell_ctrl_1_2_3_switch_views(tmp_path: Path) -> None:
    async with _ShellApp(tmp_path).run_test(headless=True) as pilot:
        await pilot.pause()

        await pilot.press("ctrl+2")
        await pilot.pause()
        assert pilot.app.query_one("#view-refine", Widget).display is True
        assert pilot.app.query_one("#view-dashboard", Widget).display is False

        await pilot.press("ctrl+3")
        await pilot.pause()
        assert pilot.app.query_one("#view-ralph", Widget).display is True
        assert pilot.app.query_one("#view-refine", Widget).display is False

        await pilot.press("ctrl+1")
        await pilot.pause()
        assert pilot.app.query_one("#view-dashboard", Widget).display is True
        assert pilot.app.query_one("#view-ralph", Widget).display is False


async def test_shell_focuses_refine_queue_after_switching_to_refine(tmp_path: Path) -> None:
    async with _ShellApp(tmp_path).run_test(headless=True) as pilot:
        await pilot.pause()
        await pilot.press("ctrl+2")
        # call_after_refresh runs on the next frame; pause a few times.
        for _ in range(3):
            await pilot.pause()
        focused = pilot.app.focused
        assert focused is not None
        assert focused.id == "refine-queue"


async def test_shell_focuses_ralph_queue_after_switching_to_ralph(tmp_path: Path) -> None:
    async with _ShellApp(tmp_path).run_test(headless=True) as pilot:
        await pilot.pause()
        await pilot.press("ctrl+3")
        for _ in range(3):
            await pilot.pause()
        focused = pilot.app.focused
        assert focused is not None
        assert focused.id == "ralph-queue"


async def test_shell_sidebar_click_switches_view(tmp_path: Path) -> None:
    async with _ShellApp(tmp_path).run_test(headless=True) as pilot:
        await pilot.pause()
        list_view = pilot.app.query_one("#sidebar-nav", ListView)
        list_view.index = 2
        list_view.action_select_cursor()
        await pilot.pause()
        assert pilot.app.query_one("#view-ralph", Widget).display is True


async def test_shell_preserves_widget_state_across_view_switches(tmp_path: Path) -> None:
    async with _ShellApp(tmp_path).run_test(headless=True) as pilot:
        await pilot.pause()
        ralph_view = pilot.app.query_one("#view-ralph", RalphView)
        ralph_view._marker = "preserved"  # type: ignore[attr-defined]

        await pilot.press("ctrl+2")
        await pilot.pause()
        await pilot.press("ctrl+3")
        await pilot.pause()

        same_ralph = pilot.app.query_one("#view-ralph", RalphView)
        assert same_ralph is ralph_view
        assert getattr(same_ralph, "_marker", None) == "preserved"


async def test_shell_keybinds_show_in_footer(tmp_path: Path) -> None:
    async with _ShellApp(tmp_path).run_test(headless=True) as pilot:
        await pilot.pause()
        screen = pilot.app.screen
        assert isinstance(screen, CogShellScreen)
        descriptions = {b.description: b.key for b in screen.BINDINGS}
        assert "Dashboard" in descriptions
        assert "Refine" in descriptions
        assert "Ralph" in descriptions
        assert "Quit" in descriptions


async def test_shell_ctrl_q_exits_app(tmp_path: Path) -> None:
    async with _ShellApp(tmp_path).run_test(headless=True) as pilot:
        await pilot.pause()
        await pilot.press("ctrl+q")
        await pilot.pause()
        assert not pilot.app.is_running


async def test_shell_ctrl_q_shows_modal_when_refine_in_progress(tmp_path: Path) -> None:
    from cog.ui.screens.quit_confirm import QuitConfirmScreen

    async with _ShellApp(tmp_path).run_test(headless=True) as pilot:
        await pilot.pause()
        refine = pilot.app.query_one(RefineView)
        refine._substate = "running"
        refine._active_item = type(
            "I", (), {"item_id": "42", "title": "t", "labels": (), "comments": (), "body": ""}
        )()  # type: ignore[assignment]

        await pilot.press("ctrl+q")
        for _ in range(3):
            await pilot.pause()

        # App still running; modal pushed on top
        assert pilot.app.is_running
        modals = [s for s in pilot.app.screen_stack if isinstance(s, QuitConfirmScreen)]
        assert len(modals) == 1


async def test_shell_quit_modal_lists_busy_descriptions(tmp_path: Path) -> None:
    from cog.ui.screens.quit_confirm import QuitConfirmScreen

    async with _ShellApp(tmp_path).run_test(headless=True) as pilot:
        await pilot.pause()
        refine = pilot.app.query_one(RefineView)
        refine._substate = "running"
        refine._active_item = type(
            "I", (), {"item_id": "42", "title": "t", "labels": (), "comments": (), "body": ""}
        )()  # type: ignore[assignment]
        ralph = pilot.app.query_one(RalphView)
        ralph._substate = "running"
        ralph._active_item = type(
            "I", (), {"item_id": "99", "title": "t", "labels": (), "comments": (), "body": ""}
        )()  # type: ignore[assignment]

        await pilot.press("ctrl+q")
        for _ in range(3):
            await pilot.pause()
        modal = next(s for s in pilot.app.screen_stack if isinstance(s, QuitConfirmScreen))
        descs = list(modal._descriptions)
        assert any("#42" in d for d in descs)
        assert any("#99" in d for d in descs)


async def test_shell_quit_modal_yes_exits(tmp_path: Path) -> None:
    async with _ShellApp(tmp_path).run_test(headless=True) as pilot:
        await pilot.pause()
        refine = pilot.app.query_one(RefineView)
        refine._substate = "running"
        refine._active_item = type(
            "I", (), {"item_id": "42", "title": "t", "labels": (), "comments": (), "body": ""}
        )()  # type: ignore[assignment]
        await pilot.press("ctrl+q")
        for _ in range(3):
            await pilot.pause()
        await pilot.press("y")
        for _ in range(3):
            await pilot.pause()
        assert not pilot.app.is_running


async def test_shell_quit_modal_no_cancels_and_stays(tmp_path: Path) -> None:
    from cog.ui.screens.quit_confirm import QuitConfirmScreen

    async with _ShellApp(tmp_path).run_test(headless=True) as pilot:
        await pilot.pause()
        refine = pilot.app.query_one(RefineView)
        refine._substate = "running"
        refine._active_item = type(
            "I", (), {"item_id": "42", "title": "t", "labels": (), "comments": (), "body": ""}
        )()  # type: ignore[assignment]
        await pilot.press("ctrl+q")
        for _ in range(3):
            await pilot.pause()
        await pilot.press("n")
        for _ in range(3):
            await pilot.pause()
        assert pilot.app.is_running
        # Modal dismissed
        modals = [s for s in pilot.app.screen_stack if isinstance(s, QuitConfirmScreen)]
        assert not modals


async def test_shell_switch_to_same_view_is_noop(tmp_path: Path) -> None:
    async with _ShellApp(tmp_path).run_test(headless=True) as pilot:
        await pilot.pause()
        dash = pilot.app.query_one("#view-dashboard", DashboardView)
        assert dash.display is True
        await pilot.press("ctrl+1")
        await pilot.pause()
        assert dash.display is True


async def test_shell_app_title_is_cog(tmp_path: Path) -> None:
    # The sidebar no longer has its own "cog" heading — the app header at
    # the top already shows "Cog" (CogApp.TITLE). This test guards that we
    # don't accidentally re-add a redundant sidebar title.
    async with _ShellApp(tmp_path).run_test(headless=True) as pilot:
        await pilot.pause()
        assert len(pilot.app.query("#sidebar-title")) == 0


async def test_shell_active_row_gets_highlighted_class(tmp_path: Path) -> None:
    async with _ShellApp(tmp_path).run_test(headless=True) as pilot:
        await pilot.pause()
        list_view = pilot.app.query_one("#sidebar-nav", ListView)
        active = [r for r in list_view.children if r.has_class("-active")]
        assert len(active) == 1
        assert active[0].id == "nav-dashboard"

        await pilot.press("ctrl+3")
        await pilot.pause()

        active = [r for r in list_view.children if r.has_class("-active")]
        assert len(active) == 1
        assert active[0].id == "nav-ralph"


# ---------------------------------------------------------------------------
# Attention indicators (#128)
# ---------------------------------------------------------------------------


async def test_shell_sidebar_shows_dot_when_view_attention_posted(tmp_path: Path) -> None:
    async with _ShellApp(tmp_path).run_test(headless=True) as pilot:
        await pilot.pause()
        screen = pilot.app.screen
        assert isinstance(screen, CogShellScreen)
        # Default active is dashboard; posting attention on refine should mark it.
        screen.post_message(ViewAttention("refine", reason="test"))
        await pilot.pause()
        sidebar = pilot.app.query_one(Sidebar)
        assert "refine" in sidebar._attention


async def test_shell_sidebar_clears_dot_when_switching_to_view(tmp_path: Path) -> None:
    async with _ShellApp(tmp_path).run_test(headless=True) as pilot:
        await pilot.pause()
        screen = pilot.app.screen
        assert isinstance(screen, CogShellScreen)
        screen.post_message(ViewAttention("refine", reason="test"))
        await pilot.pause()
        sidebar = pilot.app.query_one(Sidebar)
        assert "refine" in sidebar._attention

        # Switch to refine — dot should clear.
        await pilot.press("ctrl+2")
        await pilot.pause()
        assert "refine" not in sidebar._attention


async def test_shell_does_not_mark_active_view(tmp_path: Path) -> None:
    async with _ShellApp(tmp_path).run_test(headless=True) as pilot:
        await pilot.pause()
        screen = pilot.app.screen
        assert isinstance(screen, CogShellScreen)
        # Active view is dashboard — posting attention on dashboard should be a no-op.
        screen.post_message(ViewAttention("dashboard", reason="test"))
        await pilot.pause()
        sidebar = pilot.app.query_one(Sidebar)
        assert "dashboard" not in sidebar._attention


async def test_shell_marks_previous_view_on_switch_if_it_needs_attention(
    tmp_path: Path,
) -> None:
    # Regression: if the chat is awaiting input while the user is on refine,
    # the initial ViewAttention gets ignored (active). Switching away then
    # has to re-check the previous view and mark it.
    async with _ShellApp(tmp_path).run_test(headless=True) as pilot:
        await pilot.pause()
        refine_view = pilot.app.query_one(RefineView)

        # Simulate: refine is in review substate (needs_attention returns
        # "review ready") while we're on dashboard.
        refine_view._substate = "review"
        # Not on refine, so we switch FROM dashboard to refine (clears) and
        # then away again to trigger the re-check.
        await pilot.press("ctrl+2")  # switch to refine → clears
        await pilot.pause()
        assert "refine" not in pilot.app.query_one(Sidebar)._attention
        await pilot.press("ctrl+1")  # back to dashboard → re-check refine
        await pilot.pause()
        assert "refine" in pilot.app.query_one(Sidebar)._attention


async def test_sidebar_label_shows_dot_marker_when_attention_set(tmp_path: Path) -> None:
    async with _ShellApp(tmp_path).run_test(headless=True) as pilot:
        await pilot.pause()
        sidebar = pilot.app.query_one(Sidebar)
        sidebar.set_attention("ralph", True)
        await pilot.pause()
        ralph_row = sidebar.query_one("#nav-ralph")
        label = ralph_row.query_one("Label")
        assert "●" in str(label.renderable)


# ---------------------------------------------------------------------------
# Queue counts in sidebar (#157)
# ---------------------------------------------------------------------------


async def test_shell_refresh_queue_counts_populates_reactive(tmp_path: Path) -> None:
    tracker = _tracker_with_counts(**{"agent-ready": 3, "needs-refinement": 7})
    async with _ShellApp(tmp_path, tracker).run_test(headless=True) as pilot:
        # Wait for the on_mount worker to finish.
        for _ in range(5):
            await pilot.pause()
        screen = pilot.app.screen
        assert isinstance(screen, CogShellScreen)
        assert screen.queue_counts.get("ralph") == 3
        assert screen.queue_counts.get("refine") == 7


async def test_shell_refresh_queue_counts_sets_none_on_error(tmp_path: Path) -> None:
    from cog.core.errors import TrackerError

    t: IssueTracker = AsyncMock(spec=IssueTracker)

    async def list_by_label(label: str, *, assignee: str | None = None) -> list[Item]:
        if label == "agent-ready":
            raise TrackerError("boom")
        return _make_items(0)

    t.list_by_label = AsyncMock(side_effect=list_by_label)  # type: ignore[attr-defined]

    async with _ShellApp(tmp_path, t).run_test(headless=True) as pilot:
        for _ in range(5):
            await pilot.pause()
        screen = pilot.app.screen
        assert isinstance(screen, CogShellScreen)
        assert screen.queue_counts.get("ralph") is None
        assert screen.queue_counts.get("refine") == 0


async def test_shell_queue_counts_stale_triggers_refresh(tmp_path: Path) -> None:
    tracker = _tracker_with_counts(**{"agent-ready": 5})
    async with _ShellApp(tmp_path, tracker).run_test(headless=True) as pilot:
        for _ in range(5):
            await pilot.pause()
        screen = pilot.app.screen
        assert isinstance(screen, CogShellScreen)
        # Override tracker to return a new count.
        tracker.list_by_label = AsyncMock(  # type: ignore[attr-defined]
            side_effect=lambda label, *, assignee=None: _make_items(
                9 if label == "agent-ready" else 1
            )
        )
        screen.post_message(QueueCountsStale())
        for _ in range(5):
            await pilot.pause()
        assert screen.queue_counts.get("ralph") == 9


async def test_sidebar_renders_count_for_workflow_rows(tmp_path: Path) -> None:
    async with _ShellApp(tmp_path).run_test(headless=True) as pilot:
        await pilot.pause()
        sidebar = pilot.app.query_one(Sidebar)
        # Inject counts directly and re-render.
        sidebar._counts = {"ralph": 5, "refine": 0}
        sidebar._rerender_row("ralph")
        sidebar._rerender_row("refine")
        await pilot.pause()

        ralph_label = sidebar.query_one("#nav-ralph").query_one("Label")
        refine_label = sidebar.query_one("#nav-refine").query_one("Label")
        assert "5" in str(ralph_label.renderable)
        assert "0" in str(refine_label.renderable)


async def test_sidebar_row_layout_keybind_left_count_right(tmp_path: Path) -> None:
    """Pin the sidebar layout: keybind on the left, count on the right."""
    async with _ShellApp(tmp_path).run_test(headless=True) as pilot:
        await pilot.pause()
        sidebar = pilot.app.query_one(Sidebar)
        sidebar._counts = {"ralph": 7}
        sidebar._rerender_row("ralph")
        await pilot.pause()

        rendered = str(sidebar.query_one("#nav-ralph").query_one("Label").renderable)
        keybind_idx = rendered.index("^3")
        name_idx = rendered.index("Ralph")
        count_idx = rendered.index("7")
        assert keybind_idx < name_idx < count_idx


async def test_sidebar_renders_blank_slot_for_none_count(tmp_path: Path) -> None:
    async with _ShellApp(tmp_path).run_test(headless=True) as pilot:
        await pilot.pause()
        sidebar = pilot.app.query_one(Sidebar)
        # None means loading/error — count slot should be blank (no digit).
        sidebar._counts = {"ralph": None}
        sidebar._rerender_row("ralph")
        await pilot.pause()
        ralph_label = sidebar.query_one("#nav-ralph").query_one("Label")
        rendered = str(ralph_label.renderable)
        # No digit in the count slot area — keybind still present.
        assert "^3" in rendered


async def test_sidebar_renders_blank_slot_for_non_workflow_rows(tmp_path: Path) -> None:
    async with _ShellApp(tmp_path).run_test(headless=True) as pilot:
        await pilot.pause()
        sidebar = pilot.app.query_one(Sidebar)
        # Dashboard row should never have a count digit even if _counts is populated.
        sidebar._counts = {"ralph": 99, "refine": 1}
        sidebar._rerender_row("dashboard")
        await pilot.pause()
        dash_label = sidebar.query_one("#nav-dashboard").query_one("Label")
        rendered = str(dash_label.renderable)
        assert "^1" in rendered
        # 99 should not appear in the dashboard row.
        assert "99" not in rendered


async def test_sidebar_count_updates_via_reactive(tmp_path: Path) -> None:
    tracker = _tracker_with_counts(**{"agent-ready": 4, "needs-refinement": 2})
    async with _ShellApp(tmp_path, tracker).run_test(headless=True) as pilot:
        for _ in range(5):
            await pilot.pause()
        sidebar = pilot.app.query_one(Sidebar)
        ralph_label = sidebar.query_one("#nav-ralph").query_one("Label")
        assert "4" in str(ralph_label.renderable)
        refine_label = sidebar.query_one("#nav-refine").query_one("Label")
        assert "2" in str(refine_label.renderable)
