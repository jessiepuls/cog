"""Tests for CogShellScreen — persistent sidebar + content layout (#122)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

from textual.app import App
from textual.widget import Widget
from textual.widgets import ListView, Static

from cog.core.tracker import IssueTracker
from cog.ui.screens.shell import CogShellScreen, _StubView
from cog.ui.views.dashboard import DashboardView


def _fake_tracker() -> IssueTracker:
    t = AsyncMock(spec=IssueTracker)
    t.list_by_label = AsyncMock(return_value=[])
    return t  # type: ignore[return-value]


class _ShellApp(App):
    def __init__(self, project_dir: Path) -> None:
        super().__init__()
        self._project_dir = project_dir

    def on_mount(self) -> None:
        self.push_screen(CogShellScreen(self._project_dir, _fake_tracker()))


async def test_shell_mounts_all_views_on_startup(tmp_path: Path) -> None:
    async with _ShellApp(tmp_path).run_test(headless=True) as pilot:
        await pilot.pause()
        # Dashboard is the real view; refine/ralph are stubs.
        pilot.app.query_one("#view-dashboard", DashboardView)
        pilot.app.query_one("#view-refine", _StubView)
        pilot.app.query_one("#view-ralph", _StubView)


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
        refine_view = pilot.app.query_one("#view-refine", _StubView)
        refine_view._marker = "preserved"  # type: ignore[attr-defined]

        await pilot.press("ctrl+3")
        await pilot.pause()
        await pilot.press("ctrl+2")
        await pilot.pause()

        same_refine = pilot.app.query_one("#view-refine", _StubView)
        assert same_refine is refine_view
        assert getattr(same_refine, "_marker", None) == "preserved"


async def test_shell_keybinds_show_in_footer(tmp_path: Path) -> None:
    async with _ShellApp(tmp_path).run_test(headless=True) as pilot:
        await pilot.pause()
        screen = pilot.app.screen
        assert isinstance(screen, CogShellScreen)
        descriptions = {b.description: b.key for b in screen.BINDINGS}
        assert "Dashboard" in descriptions
        assert "Refine" in descriptions
        assert "Ralph" in descriptions


async def test_shell_switch_to_same_view_is_noop(tmp_path: Path) -> None:
    async with _ShellApp(tmp_path).run_test(headless=True) as pilot:
        await pilot.pause()
        dash = pilot.app.query_one("#view-dashboard", DashboardView)
        assert dash.display is True
        await pilot.press("ctrl+1")
        await pilot.pause()
        assert dash.display is True


async def test_shell_sidebar_title_rendered(tmp_path: Path) -> None:
    async with _ShellApp(tmp_path).run_test(headless=True) as pilot:
        await pilot.pause()
        title = pilot.app.query_one("#sidebar-title", Static)
        assert "cog" in str(title.renderable)


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
