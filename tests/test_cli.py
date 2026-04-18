import re
from pathlib import Path
from typing import Any

from typer.testing import CliRunner

from cog import __version__
from cog.cli import app
from cog.core.preflight import PreflightResult

runner = CliRunner()

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _plain(output: str) -> str:
    return _ANSI_RE.sub("", output)


def test_version():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert f"cog {__version__}" in _plain(result.output)


def test_help():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "--version" in _plain(result.output)


# --- doctor subcommand ---


class _OkCheck:
    name = "ok"
    level = "error"

    async def run(self, project_dir: Path) -> PreflightResult:
        return PreflightResult(check=self.name, ok=True, level="error", message="fine")


class _ErrorCheck:
    name = "err"
    level = "error"

    async def run(self, project_dir: Path) -> PreflightResult:
        return PreflightResult(check=self.name, ok=False, level="error", message="broken")


class _WarnCheck:
    name = "warn"
    level = "warning"

    async def run(self, project_dir: Path) -> PreflightResult:
        return PreflightResult(check=self.name, ok=False, level="warning", message="meh")


def test_doctor_all_green_exits_zero(monkeypatch, tmp_path):
    monkeypatch.setattr("cog.checks.ALL_CHECKS", (_OkCheck(),))
    result = runner.invoke(app, ["doctor", "--project-dir", str(tmp_path)])
    assert result.exit_code == 0


def test_doctor_any_error_exits_one(monkeypatch, tmp_path):
    monkeypatch.setattr("cog.checks.ALL_CHECKS", (_OkCheck(), _ErrorCheck()))
    result = runner.invoke(app, ["doctor", "--project-dir", str(tmp_path)])
    assert result.exit_code == 1


def test_doctor_warning_only_exits_zero(monkeypatch, tmp_path):
    monkeypatch.setattr("cog.checks.ALL_CHECKS", (_WarnCheck(),))
    result = runner.invoke(app, ["doctor", "--project-dir", str(tmp_path)])
    assert result.exit_code == 0


def test_doctor_output_goes_to_stderr(monkeypatch, tmp_path):
    captured: list[Any] = []

    def spy(results: Any, **kwargs: Any) -> None:
        captured.extend(results)

    monkeypatch.setattr("cog.checks.ALL_CHECKS", (_ErrorCheck(),))
    # Patch print_results so no actual stderr write happens; verify it was called.
    monkeypatch.setattr("cog.core.preflight.print_results", spy)
    result = runner.invoke(app, ["doctor", "--project-dir", str(tmp_path)])
    assert len(captured) == 1  # print_results was called with the check results
    assert result.output == ""  # nothing written directly to stdout


# --- ralph subcommand ---


def test_cog_ralph_headless_forwards_flag(monkeypatch, tmp_path):
    calls: list[dict] = []

    async def fake_build_and_run(*args: Any, **kwargs: Any) -> int:
        calls.append({"args": args, **kwargs})
        return 0

    monkeypatch.setattr("cog.cli.build_and_run", fake_build_and_run)
    result = runner.invoke(app, ["ralph", "--project-dir", str(tmp_path), "--headless"])
    assert result.exit_code == 0
    assert len(calls) == 1
    assert calls[0]["headless"] is True


def test_cog_ralph_keyboard_interrupt_exits_130(monkeypatch, tmp_path):
    import asyncio
    import inspect

    def raise_keyboard_interrupt(coro: Any) -> None:
        # Close the coroutine to prevent ResourceWarning about unawaited coroutine
        if inspect.iscoroutine(coro):
            coro.close()
        raise KeyboardInterrupt

    monkeypatch.setattr(asyncio, "run", raise_keyboard_interrupt)
    result = runner.invoke(app, ["ralph", "--project-dir", str(tmp_path), "--headless"])
    assert result.exit_code == 130
    assert "aborted." in result.output or "aborted." in (result.stderr or "")
