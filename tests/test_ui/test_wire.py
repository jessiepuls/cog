"""Tests for build_and_run factory."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from cog.core.preflight import PreflightResult
from tests.fakes import NullContentWidget


class _FakeWorkflow:
    name = "fake"
    queue_label = "fake-label"
    supports_headless = True
    preflight_checks: list = []
    content_widget_cls = NullContentWidget

    def __init__(self, **kwargs: object) -> None:
        self._kwargs = kwargs


class _RefuseHeadlessWorkflow(_FakeWorkflow):
    supports_headless = False


def _error_result() -> PreflightResult:
    return PreflightResult(check="host_tool", ok=False, level="error", message="missing tool")


async def test_build_and_run_preflight_failure_returns_one(tmp_path: Path) -> None:
    from cog.ui.wire import build_and_run

    with (
        patch("cog.ui.wire.run_checks", new=AsyncMock(return_value=[_error_result()])),
        patch("cog.ui.wire.print_results"),
    ):
        code = await build_and_run(
            _FakeWorkflow,  # type: ignore[arg-type]
            tmp_path,
            item_id=None,
            loop=False,
            headless=False,
        )
    assert code == 1


async def test_build_and_run_rejects_headless_refine_returns_two(tmp_path: Path) -> None:
    from cog.ui.wire import build_and_run

    with (
        patch("cog.ui.wire.run_checks", new=AsyncMock(return_value=[])),
        patch("cog.ui.wire.print_results"),
    ):
        code = await build_and_run(
            _RefuseHeadlessWorkflow,  # type: ignore[arg-type]
            tmp_path,
            item_id=None,
            loop=False,
            headless=True,
        )
    assert code == 2


async def test_build_and_run_preselects_item_when_item_id_given(tmp_path: Path) -> None:
    from datetime import UTC, datetime

    from cog.core.item import Item
    from cog.ui.wire import build_and_run

    fake_item = Item(
        tracker_id="gh",
        item_id="42",
        title="t",
        body="",
        labels=(),
        comments=(),
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        url="",
    )

    captured_ctx = {}

    async def _fake_run_textual(workflow, ctx, *, loop):
        captured_ctx["ctx"] = ctx
        return 0

    with (
        patch("cog.ui.wire.run_checks", new=AsyncMock(return_value=[])),
        patch("cog.ui.wire.print_results"),
        patch("cog.ui.wire.DockerSandbox", return_value=MagicMock()),
        patch("cog.ui.wire.ClaudeCliRunner", return_value=MagicMock()),
        patch("cog.ui.wire.GitHubIssueTracker") as mock_tracker_cls,
        patch("cog.ui.wire.JsonFileStateCache") as mock_cache_cls,
        patch("cog.ui.wire.TelemetryWriter"),
        patch("cog.ui.wire.project_state_dir", return_value=tmp_path / ".cog"),
        patch("cog.ui.app.run_textual", new=AsyncMock(side_effect=_fake_run_textual)),
    ):
        mock_tracker = AsyncMock()
        mock_tracker.get = AsyncMock(return_value=fake_item)
        mock_tracker_cls.return_value = mock_tracker

        mock_cache = MagicMock()
        mock_cache.was_corrupt.return_value = False
        mock_cache.is_empty.return_value = False
        mock_cache_cls.return_value = mock_cache

        await build_and_run(
            _FakeWorkflow,  # type: ignore[arg-type]
            tmp_path,
            item_id=42,
            loop=False,
            headless=False,
        )

    assert captured_ctx["ctx"].item == fake_item


async def test_build_and_run_headless_invokes_run_headless(tmp_path: Path) -> None:
    """--headless path dispatches to cog.headless.run_headless."""
    from cog.ui.wire import build_and_run

    run_headless_mock = AsyncMock(return_value=0)
    cache_mock = MagicMock()
    cache_mock.was_corrupt.return_value = False
    cache_mock.is_empty.return_value = False

    with (
        patch("cog.ui.wire.run_checks", new=AsyncMock(return_value=[])),
        patch("cog.ui.wire.print_results"),
        patch("cog.ui.wire.DockerSandbox", return_value=MagicMock()),
        patch("cog.ui.wire.ClaudeCliRunner", return_value=MagicMock()),
        patch("cog.ui.wire.GitHubIssueTracker", return_value=MagicMock()),
        patch("cog.ui.wire.JsonFileStateCache", return_value=cache_mock),
        patch("cog.ui.wire.TelemetryWriter"),
        patch("cog.ui.wire.project_state_dir", return_value=tmp_path / ".cog"),
        patch("cog.ui.wire.run_headless", run_headless_mock),
    ):
        code = await build_and_run(
            _FakeWorkflow,  # type: ignore[arg-type]
            tmp_path,
            item_id=None,
            loop=False,
            headless=True,
        )

    assert code == 0
    run_headless_mock.assert_awaited_once()


async def test_build_and_run_wires_full_stack(tmp_path: Path) -> None:
    """Verify ctx has state_cache set and run_textual is invoked."""
    from cog.ui.wire import build_and_run

    run_textual_mock = AsyncMock(return_value=0)
    cache_mock = MagicMock()
    cache_mock.was_corrupt.return_value = False
    cache_mock.is_empty.return_value = False

    with (
        patch("cog.ui.wire.run_checks", new=AsyncMock(return_value=[])),
        patch("cog.ui.wire.print_results"),
        patch("cog.ui.wire.DockerSandbox", return_value=MagicMock()),
        patch("cog.ui.wire.ClaudeCliRunner", return_value=MagicMock()),
        patch("cog.ui.wire.GitHubIssueTracker", return_value=MagicMock()),
        patch("cog.ui.wire.JsonFileStateCache", return_value=cache_mock),
        patch("cog.ui.wire.TelemetryWriter"),
        patch("cog.ui.wire.project_state_dir", return_value=tmp_path / ".cog"),
        patch("cog.ui.app.run_textual", run_textual_mock),
    ):
        code = await build_and_run(
            _FakeWorkflow,  # type: ignore[arg-type]
            tmp_path,
            item_id=None,
            loop=False,
            headless=False,
        )

    assert code == 0
    run_textual_mock.assert_awaited_once()
    _, call_ctx = run_textual_mock.call_args[0]
    assert call_ctx.state_cache is cache_mock
