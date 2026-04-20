"""Tests for CogShellScreen — persistent sidebar + content layout (#122)."""

from __future__ import annotations

from textual.app import App
from textual.widgets import ListView, Static

from cog.ui.screens.shell import CogShellScreen, _StubView


class _ShellApp(App):
    def on_mount(self) -> None:
        self.push_screen(CogShellScreen())


async def test_shell_mounts_all_views_on_startup() -> None:
    async with _ShellApp().run_test(headless=True) as pilot:
        await pilot.pause()
        for view_id in ("dashboard", "refine", "ralph"):
            # Raises if not present.
            pilot.app.query_one(f"#view-{view_id}", _StubView)


async def test_shell_displays_only_active_view() -> None:
    async with _ShellApp().run_test(headless=True) as pilot:
        await pilot.pause()
        displayed = [
            v
            for v in ("dashboard", "refine", "ralph")
            if pilot.app.query_one(f"#view-{v}", _StubView).display
        ]
        assert displayed == ["dashboard"]  # default active


async def test_shell_ctrl_1_2_3_switch_views() -> None:
    async with _ShellApp().run_test(headless=True) as pilot:
        await pilot.pause()

        await pilot.press("ctrl+2")
        await pilot.pause()
        assert pilot.app.query_one("#view-refine", _StubView).display is True
        assert pilot.app.query_one("#view-dashboard", _StubView).display is False

        await pilot.press("ctrl+3")
        await pilot.pause()
        assert pilot.app.query_one("#view-ralph", _StubView).display is True
        assert pilot.app.query_one("#view-refine", _StubView).display is False

        await pilot.press("ctrl+1")
        await pilot.pause()
        assert pilot.app.query_one("#view-dashboard", _StubView).display is True
        assert pilot.app.query_one("#view-ralph", _StubView).display is False


async def test_shell_sidebar_click_switches_view() -> None:
    async with _ShellApp().run_test(headless=True) as pilot:
        await pilot.pause()
        list_view = pilot.app.query_one("#sidebar-nav", ListView)
        # Select the ralph row (index 2)
        list_view.index = 2
        # Fire the selection event as clicking would
        list_view.action_select_cursor()
        await pilot.pause()
        assert pilot.app.query_one("#view-ralph", _StubView).display is True


async def test_shell_preserves_widget_state_across_view_switches() -> None:
    # The whole point of the shell: widgets stay mounted when inactive.
    async with _ShellApp().run_test(headless=True) as pilot:
        await pilot.pause()
        refine_view = pilot.app.query_one("#view-refine", _StubView)

        # Set a marker attribute on the inactive view's widget.
        refine_view._marker = "preserved"  # type: ignore[attr-defined]

        # Switch away and back.
        await pilot.press("ctrl+3")
        await pilot.pause()
        await pilot.press("ctrl+2")
        await pilot.pause()

        # Widget instance is the same (not re-created) — marker survives.
        same_refine = pilot.app.query_one("#view-refine", _StubView)
        assert same_refine is refine_view
        assert getattr(same_refine, "_marker", None) == "preserved"


async def test_shell_keybinds_show_in_footer() -> None:
    async with _ShellApp().run_test(headless=True) as pilot:
        await pilot.pause()
        # The Footer widget pulls descriptions from bindings. Rather than
        # inspecting footer rendering (Textual-version-sensitive), assert
        # the screen exposes the bindings with shown descriptions.
        screen = pilot.app.screen
        assert isinstance(screen, CogShellScreen)
        descriptions = {b.description: b.key for b in screen.BINDINGS}
        assert "Dashboard" in descriptions
        assert "Refine" in descriptions
        assert "Ralph" in descriptions


async def test_shell_switch_to_same_view_is_noop() -> None:
    async with _ShellApp().run_test(headless=True) as pilot:
        await pilot.pause()
        dash = pilot.app.query_one("#view-dashboard", _StubView)
        assert dash.display is True
        # Already on dashboard; pressing ctrl+1 shouldn't toggle anything off.
        await pilot.press("ctrl+1")
        await pilot.pause()
        assert dash.display is True


async def test_shell_sidebar_title_rendered() -> None:
    async with _ShellApp().run_test(headless=True) as pilot:
        await pilot.pause()
        title = pilot.app.query_one("#sidebar-title", Static)
        assert "cog" in str(title.renderable)


async def test_shell_active_row_gets_highlighted_class() -> None:
    async with _ShellApp().run_test(headless=True) as pilot:
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
