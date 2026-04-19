"""Tests for PreflightScreen."""

from pathlib import Path
from unittest.mock import AsyncMock, patch

from textual.app import App, ComposeResult

from cog.core.preflight import PreflightResult
from cog.ui.preflight import PreflightScreen


def _ok_result(check: str = "docker") -> PreflightResult:
    return PreflightResult(check=check, ok=True, level="error", message="ok")


def _error_result(check: str = "docker") -> PreflightResult:
    return PreflightResult(check=check, ok=False, level="error", message="docker not running")


def _warning_result(check: str = "clean_tree") -> PreflightResult:
    return PreflightResult(check=check, ok=False, level="warning", message="uncommitted changes")


class _FakeWorkflow:
    preflight_checks: list = []


def _make_preflight_app(
    results: list[PreflightResult],
    result_holder: list[bool],
) -> App:
    class _TestApp(App):
        def compose(self) -> ComposeResult:
            return iter([])

        def on_mount(self) -> None:
            self.push_screen(
                PreflightScreen(_FakeWorkflow, Path("/tmp")),  # noqa: S108
                callback=lambda r: (result_holder.append(r), self.exit()),
            )

    return _TestApp()


async def test_preflight_screen_dismisses_true_when_all_checks_pass() -> None:
    result_holder: list[bool] = []
    app = _make_preflight_app([_ok_result()], result_holder)

    with patch("cog.ui.preflight.run_checks", new=AsyncMock(return_value=[_ok_result()])):
        async with app.run_test(headless=True) as pilot:
            await pilot.pause(delay=0.1)
            await pilot.pause(delay=0.6)

    assert result_holder == [True]


async def test_preflight_screen_does_not_auto_dismiss_on_failure() -> None:
    class _TestApp(App):
        def compose(self) -> ComposeResult:
            return iter([])

        def on_mount(self) -> None:
            self.push_screen(PreflightScreen(_FakeWorkflow, Path("/tmp")))  # noqa: S108

    with patch("cog.ui.preflight.run_checks", new=AsyncMock(return_value=[_error_result()])):
        async with _TestApp().run_test(headless=True) as pilot:
            await pilot.pause(delay=0.1)
            await pilot.pause(delay=0.6)
            top_screen = pilot.app.screen
            assert isinstance(top_screen, PreflightScreen)


async def test_preflight_screen_dismisses_false_on_q() -> None:
    result_holder: list[bool] = []

    class _TestApp(App):
        def compose(self) -> ComposeResult:
            return iter([])

        def on_mount(self) -> None:
            self.push_screen(
                PreflightScreen(_FakeWorkflow, Path("/tmp")),  # noqa: S108
                callback=lambda r: (result_holder.append(r), self.exit()),
            )

    with patch("cog.ui.preflight.run_checks", new=AsyncMock(return_value=[_error_result()])):
        async with _TestApp().run_test(headless=True) as pilot:
            await pilot.pause()
            await pilot.press("q")
            await pilot.pause()

    assert result_holder == [False]


async def test_preflight_screen_dismisses_false_on_escape() -> None:
    result_holder: list[bool] = []

    class _TestApp(App):
        def compose(self) -> ComposeResult:
            return iter([])

        def on_mount(self) -> None:
            self.push_screen(
                PreflightScreen(_FakeWorkflow, Path("/tmp")),  # noqa: S108
                callback=lambda r: (result_holder.append(r), self.exit()),
            )

    with patch("cog.ui.preflight.run_checks", new=AsyncMock(return_value=[_error_result()])):
        async with _TestApp().run_test(headless=True) as pilot:
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()

    assert result_holder == [False]


async def test_preflight_screen_renders_check_results() -> None:
    results = [_ok_result("gh_auth"), _error_result("docker")]

    class _TestApp(App):
        def compose(self) -> ComposeResult:
            return iter([])

        def on_mount(self) -> None:
            self.push_screen(PreflightScreen(_FakeWorkflow, Path("/tmp")))  # noqa: S108

    with patch("cog.ui.preflight.run_checks", new=AsyncMock(return_value=results)):
        async with _TestApp().run_test(headless=True) as pilot:
            await pilot.pause()
            from textual.widgets import Static

            statics = pilot.app.query(Static)
            rendered_text = " ".join(str(s.renderable) for s in statics)

    assert "gh_auth" in rendered_text
    assert "docker" in rendered_text


async def test_preflight_screen_warning_level_does_not_block_auto_dismiss() -> None:
    results = [_ok_result(), _warning_result()]
    result_holder: list[bool] = []
    app = _make_preflight_app(results, result_holder)

    with patch("cog.ui.preflight.run_checks", new=AsyncMock(return_value=results)):
        async with app.run_test(headless=True) as pilot:
            await pilot.pause(delay=0.1)
            await pilot.pause(delay=0.6)

    assert result_holder == [True]
