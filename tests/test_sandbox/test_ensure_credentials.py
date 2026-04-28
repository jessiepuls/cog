"""Tests for ensure_credentials and _existing_credentials_still_valid."""

import json
import time
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from cog.runners.docker_sandbox import ensure_credentials

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class FakeProc:
    def __init__(self, returncode: int = 0, stdout: bytes = b"") -> None:
        self.returncode = returncode
        self._stdout = stdout

    async def communicate(self) -> tuple[bytes, bytes]:
        return self._stdout, b""


def _patch_exec(proc: FakeProc) -> Any:
    async def impl(*_args: Any, **_kwargs: Any) -> FakeProc:
        return proc

    return patch(
        "cog.runners.docker_sandbox.asyncio.create_subprocess_exec",
        new=AsyncMock(side_effect=impl),
    )


def _patch_which(has_security: bool = True) -> Any:
    def which(name: str) -> str | None:
        return "/usr/bin/security" if name == "security" and has_security else None

    return patch("cog.runners.docker_sandbox.shutil.which", side_effect=which)


def _write_valid_creds(home: Path, expires_offset_ms: int = 60 * 60 * 1000) -> None:
    """Write a credentials file with expiresAt = now + offset (default 1h)."""
    creds_dir = home / ".claude"
    creds_dir.mkdir(parents=True, exist_ok=True)
    expires_at = int(time.time() * 1000) + expires_offset_ms
    data = {"claudeAiOauth": {"expiresAt": expires_at}}
    (creds_dir / ".credentials.json").write_text(json.dumps(data))


# ---------------------------------------------------------------------------
# ANTHROPIC_API_KEY short-circuit
# ---------------------------------------------------------------------------


async def test_api_key_set_returns_immediately(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    security_called = False

    async def no_security(*_args: Any, **_kwargs: Any) -> FakeProc:
        nonlocal security_called
        security_called = True
        return FakeProc(0)

    with (
        _patch_which(),
        patch(
            "cog.runners.docker_sandbox.asyncio.create_subprocess_exec",
            new=AsyncMock(side_effect=no_security),
        ),
        patch("cog.runners.docker_sandbox.Path.home", return_value=tmp_path),
    ):
        result = await ensure_credentials()

    assert result == "api_key_set"
    assert not security_called


async def test_api_key_set_force_true_still_returns_immediately(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    security_called = False

    async def no_security(*_args: Any, **_kwargs: Any) -> FakeProc:
        nonlocal security_called
        security_called = True
        return FakeProc(0)

    with (
        _patch_which(),
        patch(
            "cog.runners.docker_sandbox.asyncio.create_subprocess_exec",
            new=AsyncMock(side_effect=no_security),
        ),
        patch("cog.runners.docker_sandbox.Path.home", return_value=tmp_path),
    ):
        result = await ensure_credentials(force=True)

    assert result == "api_key_set"
    assert not security_called


# ---------------------------------------------------------------------------
# Skip-when-valid logic (force=False)
# ---------------------------------------------------------------------------


async def test_valid_creds_skips_security_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    _write_valid_creds(tmp_path)
    security_called = False

    async def no_security(*_args: Any, **_kwargs: Any) -> FakeProc:
        nonlocal security_called
        security_called = True
        return FakeProc(0)

    with (
        _patch_which(),
        patch(
            "cog.runners.docker_sandbox.asyncio.create_subprocess_exec",
            new=AsyncMock(side_effect=no_security),
        ),
        patch("cog.runners.docker_sandbox.Path.home", return_value=tmp_path),
    ):
        result = await ensure_credentials(force=False)

    assert result == "skipped"
    assert not security_called


async def test_valid_creds_force_true_invokes_security(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    _write_valid_creds(tmp_path)
    creds_data = b'{"claudeAiOauth": {"expiresAt": 9999999999999}}'

    with (
        _patch_which(),
        _patch_exec(FakeProc(0, creds_data)),
        patch("cog.runners.docker_sandbox._write_credentials") as mock_write,
        patch("cog.runners.docker_sandbox.Path.home", return_value=tmp_path),
    ):
        result = await ensure_credentials(force=True)

    assert result == "refreshed"
    mock_write.assert_called_once_with(creds_data)


async def test_expired_creds_invokes_security(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    # expires 1 minute ago
    _write_valid_creds(tmp_path, expires_offset_ms=-60 * 1000)
    creds_data = b'{"claudeAiOauth": {"expiresAt": 9999999999999}}'

    with (
        _patch_which(),
        _patch_exec(FakeProc(0, creds_data)),
        patch("cog.runners.docker_sandbox._write_credentials") as mock_write,
        patch("cog.runners.docker_sandbox.Path.home", return_value=tmp_path),
    ):
        result = await ensure_credentials()

    assert result == "refreshed"
    mock_write.assert_called_once_with(creds_data)


@pytest.mark.parametrize(
    "bad_content",
    [
        pytest.param(b"not json at all", id="malformed_json"),
        pytest.param(b"{}", id="missing_claudeAiOauth_key"),
        pytest.param(b'{"claudeAiOauth": {}}', id="missing_expiresAt_key"),
        pytest.param(b'{"claudeAiOauth": {"expiresAt": "not-an-int"}}', id="expiresAt_not_int"),
    ],
)
async def test_invalid_creds_file_falls_through_to_refresh(
    bad_content: bytes, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    creds_dir = tmp_path / ".claude"
    creds_dir.mkdir(parents=True)
    (creds_dir / ".credentials.json").write_bytes(bad_content)
    creds_data = b'{"claudeAiOauth": {"expiresAt": 9999999999999}}'

    with (
        _patch_which(),
        _patch_exec(FakeProc(0, creds_data)),
        patch("cog.runners.docker_sandbox._write_credentials") as mock_write,
        patch("cog.runners.docker_sandbox.Path.home", return_value=tmp_path),
    ):
        result = await ensure_credentials()

    assert result == "refreshed"
    mock_write.assert_called_once()


async def test_missing_creds_file_falls_through_to_refresh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    # No credentials file written
    creds_data = b'{"claudeAiOauth": {"expiresAt": 9999999999999}}'

    with (
        _patch_which(),
        _patch_exec(FakeProc(0, creds_data)),
        patch("cog.runners.docker_sandbox._write_credentials") as mock_write,
        patch("cog.runners.docker_sandbox.Path.home", return_value=tmp_path),
    ):
        result = await ensure_credentials()

    assert result == "refreshed"
    mock_write.assert_called_once()


# ---------------------------------------------------------------------------
# security binary / keychain errors
# ---------------------------------------------------------------------------


async def test_security_missing_returns_sentinel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    with (
        _patch_which(has_security=False),
        patch("cog.runners.docker_sandbox.Path.home", return_value=tmp_path),
    ):
        result = await ensure_credentials()

    assert result == "security_missing"


async def test_keychain_missing_returns_sentinel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    with (
        _patch_which(),
        _patch_exec(FakeProc(1)),  # security exits non-zero
        patch("cog.runners.docker_sandbox.Path.home", return_value=tmp_path),
    ):
        result = await ensure_credentials()

    assert result == "keychain_missing"


# ---------------------------------------------------------------------------
# stderr output
# ---------------------------------------------------------------------------


async def test_refresh_path_prints_refreshing_line(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    creds_data = b'{"claudeAiOauth": {"expiresAt": 9999999999999}}'

    with (
        _patch_which(),
        _patch_exec(FakeProc(0, creds_data)),
        patch("cog.runners.docker_sandbox._write_credentials"),
        patch("cog.runners.docker_sandbox.Path.home", return_value=tmp_path),
    ):
        result = await ensure_credentials()

    assert result == "refreshed"
    assert "refreshing Claude Code credentials from keychain" in capsys.readouterr().err


async def test_skip_path_is_silent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    _write_valid_creds(tmp_path)

    with (
        _patch_which(),
        patch("cog.runners.docker_sandbox.Path.home", return_value=tmp_path),
    ):
        result = await ensure_credentials()

    assert result == "skipped"
    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out == ""
