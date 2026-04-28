"""Tests for the `cog auth refresh` CLI subcommand."""

import json
import time
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

from typer.testing import CliRunner

from cog.cli import app

runner = CliRunner()

_FAKE_EXPIRY_MSG = "refreshed credentials from keychain (expires 2026-01-01 00:00:00 UTC)"


def _write_creds(home: Path, expires_offset_ms: int = 60 * 60 * 1000) -> None:
    creds_dir = home / ".claude"
    creds_dir.mkdir(parents=True, exist_ok=True)
    expires_at = int(time.time() * 1000) + expires_offset_ms
    data = {"claudeAiOauth": {"expiresAt": expires_at}}
    (creds_dir / ".credentials.json").write_text(json.dumps(data))


def _patch_ensure(result: str) -> Any:
    mock = AsyncMock(return_value=result)
    return patch("cog.runners.docker_sandbox.ensure_credentials", mock)


def _patch_expiry() -> Any:
    return patch("cog.cli._format_expiry_message", return_value=_FAKE_EXPIRY_MSG)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_auth_refresh_success_exits_zero() -> None:
    with _patch_ensure("refreshed"), _patch_expiry():
        result = runner.invoke(app, ["auth", "refresh"])

    assert result.exit_code == 0


def test_auth_refresh_success_prints_expiry_message() -> None:
    with _patch_ensure("refreshed"), _patch_expiry():
        result = runner.invoke(app, ["auth", "refresh"])

    assert _FAKE_EXPIRY_MSG in result.output


# ---------------------------------------------------------------------------
# ANTHROPIC_API_KEY set
# ---------------------------------------------------------------------------


def test_auth_refresh_api_key_set_exits_zero() -> None:
    with _patch_ensure("api_key_set"):
        result = runner.invoke(app, ["auth", "refresh"])

    assert result.exit_code == 0


def test_auth_refresh_api_key_set_prints_message() -> None:
    with _patch_ensure("api_key_set"):
        result = runner.invoke(app, ["auth", "refresh"])

    assert "ANTHROPIC_API_KEY is set" in result.output


# ---------------------------------------------------------------------------
# Error: security binary missing
# ---------------------------------------------------------------------------


def test_auth_refresh_security_missing_exits_one() -> None:
    with _patch_ensure("security_missing"):
        result = runner.invoke(app, ["auth", "refresh"])

    assert result.exit_code == 1


def test_auth_refresh_security_missing_mentions_security_binary() -> None:
    with _patch_ensure("security_missing"):
        result = runner.invoke(app, ["auth", "refresh"])

    assert "security" in result.output.lower()


# ---------------------------------------------------------------------------
# Error: keychain entry missing
# ---------------------------------------------------------------------------


def test_auth_refresh_keychain_missing_exits_one() -> None:
    with _patch_ensure("keychain_missing"):
        result = runner.invoke(app, ["auth", "refresh"])

    assert result.exit_code == 1


def test_auth_refresh_keychain_missing_message_mentions_claude() -> None:
    with _patch_ensure("keychain_missing"):
        result = runner.invoke(app, ["auth", "refresh"])

    assert "claude" in result.output.lower()


# ---------------------------------------------------------------------------
# ensure_credentials called with force=True
# ---------------------------------------------------------------------------


def test_auth_refresh_calls_ensure_with_force_true() -> None:
    mock = AsyncMock(return_value="refreshed")

    with patch("cog.runners.docker_sandbox.ensure_credentials", mock), _patch_expiry():
        runner.invoke(app, ["auth", "refresh"])

    mock.assert_awaited_once_with(force=True)


# ---------------------------------------------------------------------------
# Expiry fallback when creds file unreadable after refresh
# ---------------------------------------------------------------------------


def test_auth_refresh_fallback_message_when_no_creds_file(tmp_path: Path) -> None:
    # No credentials file on disk — _format_expiry_message falls back to base message
    with (
        _patch_ensure("refreshed"),
        patch("cog.cli.Path.home", return_value=tmp_path),
    ):
        result = runner.invoke(app, ["auth", "refresh"])

    assert result.exit_code == 0
    assert "refreshed credentials from keychain" in result.output


# ---------------------------------------------------------------------------
# Expiry timestamp in success message
# ---------------------------------------------------------------------------


def test_auth_refresh_success_includes_expiry_timestamp(tmp_path: Path) -> None:
    _write_creds(tmp_path, expires_offset_ms=2 * 60 * 60 * 1000)

    with (
        _patch_ensure("refreshed"),
        patch("cog.cli.Path.home", return_value=tmp_path),
    ):
        result = runner.invoke(app, ["auth", "refresh"])

    assert "expires" in result.output
