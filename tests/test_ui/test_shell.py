"""Tests for CogShellScreen — sidebar + content layout.

Static views: Dashboard (^1), Issues (^2), Chat (^3). Dynamic per-run
slots are tested in `test_shell_dynamic_slots.py`.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

from textual.app import App
from textual.widget import Widget
from textual.widgets import ListView

from cog.core.tracker import IssueTracker
from cog.ui.messages import ViewAttention
from cog.ui.screens.shell import CogShellScreen, Sidebar
from cog.ui.views.chat import ChatView
from cog.ui.views.dashboard import DashboardView
from cog.ui.views.issues import IssuesView


def _fake_tracker() -> IssueTracker:
    t = AsyncMock(spec=IssueTracker)
    t.list_by_label = AsyncMock(return_value=[])
    return t  # type: ignore[return-value]


class _ShellApp(App):
    def __init__(self, project_dir: Path, tracker: IssueTracker | None = None) -> None:
        super().__init__()
        self._project_dir = project_dir
        self._tracker = tracker or _fake_tracker()

    def on_mount(self) -> None:
        self.push_screen(CogShellScreen(self._project_dir, self._tracker))


async def test_shell_mounts_static_views(tmp_path: Path) -> None:
    async with _ShellApp(tmp_path).run_test(headless=True) as pilot:
        await pilot.pause()
        pilot.app.query_one("#view-dashboard", DashboardView)
        pilot.app.query_one("#view-issues", IssuesView)
        pilot.app.query_one("#view-chat", ChatView)


async def test_shell_displays_only_active_view(tmp_path: Path) -> None:
    async with _ShellApp(tmp_path).run_test(headless=True) as pilot:
        await pilot.pause()
        displayed = [
            v
            for v in ("dashboard", "issues", "chat")
            if pilot.app.query_one(f"#view-{v}", Widget).display
        ]
        assert displayed == ["dashboard"]


async def test_shell_keybinds_switch_views(tmp_path: Path) -> None:
    async with _ShellApp(tmp_path).run_test(headless=True) as pilot:
        await pilot.pause()

        await pilot.press("ctrl+2")
        await pilot.pause()
        assert pilot.app.query_one("#view-issues", Widget).display is True
        assert pilot.app.query_one("#view-dashboard", Widget).display is False

        await pilot.press("ctrl+3")
        await pilot.pause()
        assert pilot.app.query_one("#view-chat", Widget).display is True
        assert pilot.app.query_one("#view-issues", Widget).display is False

        await pilot.press("ctrl+1")
        await pilot.pause()
        assert pilot.app.query_one("#view-dashboard", Widget).display is True


async def test_shell_sidebar_click_switches_view(tmp_path: Path) -> None:
    async with _ShellApp(tmp_path).run_test(headless=True) as pilot:
        await pilot.pause()
        list_view = pilot.app.query_one("#sidebar-nav", ListView)
        list_view.index = 1  # issues row
        list_view.action_select_cursor()
        await pilot.pause()
        assert pilot.app.query_one("#view-issues", Widget).display is True


async def test_shell_ctrl_q_exits_when_idle(tmp_path: Path) -> None:
    async with _ShellApp(tmp_path).run_test(headless=True) as pilot:
        await pilot.pause()
        await pilot.press("ctrl+q")
        await pilot.pause()
        assert not pilot.app.is_running


async def test_shell_active_row_gets_highlighted_class(tmp_path: Path) -> None:
    async with _ShellApp(tmp_path).run_test(headless=True) as pilot:
        await pilot.pause()
        list_view = pilot.app.query_one("#sidebar-nav", ListView)
        # Default: dashboard is active
        active = [c for c in list_view.children if c.has_class("-active")]
        assert len(active) == 1
        assert active[0].id == "nav-dashboard"

        await pilot.press("ctrl+2")
        await pilot.pause()
        active = [c for c in list_view.children if c.has_class("-active")]
        assert len(active) == 1
        assert active[0].id == "nav-issues"


async def test_sidebar_dot_marker_on_attention(tmp_path: Path) -> None:
    async with _ShellApp(tmp_path).run_test(headless=True) as pilot:
        await pilot.pause()
        sidebar = pilot.app.query_one(Sidebar)

        # Post attention for issues view (we're on dashboard)
        sidebar.post_message(ViewAttention(view_id="issues", reason="test"))
        await pilot.pause()

        list_view = pilot.app.query_one("#sidebar-nav", ListView)
        issues_row = next(c for c in list_view.children if c.id == "nav-issues")
        assert "●" in str(issues_row.query_one("Label").renderable)


async def test_sidebar_clears_dot_when_switching_to_view(tmp_path: Path) -> None:
    async with _ShellApp(tmp_path).run_test(headless=True) as pilot:
        await pilot.pause()
        sidebar = pilot.app.query_one(Sidebar)

        sidebar.post_message(ViewAttention(view_id="issues", reason="test"))
        await pilot.pause()
        await pilot.press("ctrl+2")
        await pilot.pause()

        list_view = pilot.app.query_one("#sidebar-nav", ListView)
        issues_row = next(c for c in list_view.children if c.id == "nav-issues")
        assert "●" not in str(issues_row.query_one("Label").renderable)
