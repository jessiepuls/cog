"""Tests for CLI dispatch — ralph/refine subcommands and main menu invocation."""

import re
from unittest.mock import AsyncMock, patch

from typer.testing import CliRunner

from cog.cli import app

runner = CliRunner()

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _plain(text: str) -> str:
    return _ANSI_RE.sub("", text)


def test_cog_no_args_invokes_main_menu() -> None:
    with patch("cog.ui.app._run_main_menu", new=AsyncMock(return_value=None)) as mock_menu:
        result = runner.invoke(app, [])
        assert result.exit_code == 0
        mock_menu.assert_called_once()


def test_cog_ralph_dispatches_ralph_class_to_build_and_run() -> None:
    # build_and_run is imported at module level in cli.py, so patch it there
    build_mock = AsyncMock(return_value=0)
    with patch("cog.cli.build_and_run", build_mock):
        result = runner.invoke(app, ["ralph"])
    assert result.exit_code == 0
    build_mock.assert_awaited_once()
    called_cls = build_mock.call_args[0][0]
    assert called_cls.__name__ == "RalphWorkflow"


def test_cog_refine_rejects_headless_flag() -> None:
    # --headless is not a valid option for `cog refine` — typer exits 2
    result = runner.invoke(app, ["refine", "--headless"])
    assert result.exit_code == 2


def test_cog_ralph_loop_forwards_flag() -> None:
    build_mock = AsyncMock(return_value=0)
    with patch("cog.cli.build_and_run", build_mock):
        result = runner.invoke(app, ["ralph", "--loop"])
    assert result.exit_code == 0
    _, kwargs = build_mock.call_args
    assert kwargs.get("loop") is True


def test_cog_item_flag_forwards() -> None:
    build_mock = AsyncMock(return_value=0)
    with patch("cog.cli.build_and_run", build_mock):
        result = runner.invoke(app, ["ralph", "--item", "42"])
    assert result.exit_code == 0
    _, kwargs = build_mock.call_args
    assert kwargs.get("item_id") == 42
