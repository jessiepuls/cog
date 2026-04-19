"""Unit tests for DockerSandbox — all docker/security calls are mocked."""

import os
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from cog.core.errors import DockerImageBuildError, DockerUnavailableError, SandboxError
from cog.runners.docker_sandbox import _EXPECTED_IMAGE_VERSION, DockerSandbox

_PATCH_DOCKERFILE = "cog.runners.docker_sandbox._read_bundled_dockerfile"
_FAKE_DOCKERFILE = lambda: b"FROM scratch\n"  # noqa: E731


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class FakeProc:
    def __init__(self, returncode: int = 0, stdout: bytes = b"") -> None:
        self.returncode = returncode
        self._stdout = stdout

    async def wait(self) -> int:
        return self.returncode

    async def communicate(self) -> tuple[bytes, bytes]:
        return self._stdout, b""


def _exec_mock(*procs: FakeProc) -> AsyncMock:
    """Return an AsyncMock that yields FakeProcs in order (last one repeated)."""
    queue = list(procs)

    async def impl(*_args: Any, **_kwargs: Any) -> FakeProc:
        return queue.pop(0) if len(queue) > 1 else queue[0]

    return AsyncMock(side_effect=impl)


def _standard_build_procs(*, image_exists: bool, build_rc: int = 0) -> list[FakeProc]:
    """Ordered procs for one full _ensure_image_built call."""
    inspect_stdout = f"{_EXPECTED_IMAGE_VERSION}\n".encode() if image_exists else b""
    return [
        FakeProc(0),  # docker info
        FakeProc(0 if image_exists else 1, inspect_stdout),  # docker image inspect
        FakeProc(build_rc),  # docker build
    ]


def _patch_exec(*procs: FakeProc) -> Any:
    return patch(
        "cog.runners.docker_sandbox.asyncio.create_subprocess_exec",
        new=_exec_mock(*procs),
    )


def _patch_which(has_docker: bool = True, has_security: bool = True) -> Any:
    def which(name: str) -> str | None:
        if name == "docker":
            return "/usr/bin/docker" if has_docker else None
        if name == "security":
            return "/usr/bin/security" if has_security else None
        return None

    return patch("cog.runners.docker_sandbox.shutil.which", side_effect=which)


# ---------------------------------------------------------------------------
# Build-once caching
# ---------------------------------------------------------------------------


async def test_build_once_per_instance(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(_PATCH_DOCKERFILE, _FAKE_DOCKERFILE)

    all_calls: list[tuple[str, ...]] = []

    async def tracking_exec(*args: str, **kwargs: Any) -> FakeProc:
        all_calls.append(args)
        # docker info, image inspect(exists), build → 0; security → success
        return FakeProc(0, b'{"t":"x"}')

    with (
        _patch_which(),
        patch(
            "cog.runners.docker_sandbox.asyncio.create_subprocess_exec",
            new=AsyncMock(side_effect=tracking_exec),
        ),
        patch("cog.runners.docker_sandbox._write_credentials"),
        patch("cog.runners.docker_sandbox.Path.home", return_value=tmp_path),
    ):
        sandbox = DockerSandbox()
        await sandbox.prepare()
        await sandbox.prepare()
        await sandbox.prepare()

    build_calls = [c for c in all_calls if c[0] == "docker" and len(c) > 1 and c[1] == "build"]
    security_calls = [c for c in all_calls if c[0] == "security"]
    assert len(build_calls) == 1
    assert len(security_calls) == 3


async def test_first_build_uses_visible_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(_PATCH_DOCKERFILE, _FAKE_DOCKERFILE)

    captured: list[tuple[str, ...]] = []

    async def tracking_exec(*args: str, **kwargs: Any) -> FakeProc:
        captured.append(args)
        # info ok, inspect → 1 (missing), build ok, security ok
        idx = len(captured) - 1
        rc = 1 if idx == 1 else 0
        return FakeProc(rc, b'{"t":"x"}')

    with (
        _patch_which(),
        patch(
            "cog.runners.docker_sandbox.asyncio.create_subprocess_exec",
            new=AsyncMock(side_effect=tracking_exec),
        ),
        patch("cog.runners.docker_sandbox._write_credentials"),
        patch("cog.runners.docker_sandbox.Path.home", return_value=tmp_path),
    ):
        sandbox = DockerSandbox()
        await sandbox.prepare()

    build_args = next(c for c in captured if c[0] == "docker" and len(c) > 1 and c[1] == "build")
    assert "--quiet" not in build_args


async def test_cached_build_uses_quiet_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(_PATCH_DOCKERFILE, _FAKE_DOCKERFILE)

    captured: list[tuple[str, ...]] = []

    async def tracking_exec(*args: str, **kwargs: Any) -> FakeProc:
        captured.append(args)
        # image inspect returns matching version label → image exists → quiet build
        if len(args) > 2 and args[1] == "image" and args[2] == "inspect":
            return FakeProc(0, f"{_EXPECTED_IMAGE_VERSION}\n".encode())
        return FakeProc(0, b'{"t":"x"}')

    with (
        _patch_which(),
        patch(
            "cog.runners.docker_sandbox.asyncio.create_subprocess_exec",
            new=AsyncMock(side_effect=tracking_exec),
        ),
        patch("cog.runners.docker_sandbox._write_credentials"),
        patch("cog.runners.docker_sandbox.Path.home", return_value=tmp_path),
    ):
        sandbox = DockerSandbox()
        await sandbox.prepare()

    build_args = next(c for c in captured if c[0] == "docker" and len(c) > 1 and c[1] == "build")
    assert "--quiet" in build_args


# ---------------------------------------------------------------------------
# Mount args
# ---------------------------------------------------------------------------


def _make_home(tmp_path: Path, *, skip: set[str] | None = None) -> Path:
    skip = skip or set()
    paths = {
        ".claude": True,
        ".claude.json": False,  # file, not dir
        ".config/gh": True,
        ".gitconfig": False,
        ".local/state/cog": True,
    }
    for rel, is_dir in paths.items():
        if rel in skip:
            continue
        target = tmp_path / rel
        if is_dir:
            target.mkdir(parents=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.touch()
    return tmp_path


def test_mount_args_standard_set(tmp_path: Path) -> None:
    home = _make_home(tmp_path)
    with (
        patch("cog.runners.docker_sandbox.Path.home", return_value=home),
        patch("cog.runners.docker_sandbox.Path.cwd", return_value=Path("/work")),
    ):
        sandbox = DockerSandbox()
        args = sandbox._mount_args()

    pairs = [args[i + 1] for i in range(0, len(args), 2) if args[i] == "-v"]
    containers = [p.split(":")[1] for p in pairs]
    assert "/work" in containers
    assert "/tmp/cog-home/.claude" in containers
    assert "/tmp/cog-home/.claude.json" in containers
    assert "/tmp/cog-home/.config/gh" in containers
    assert "/tmp/cog-home/.gitconfig" in containers
    assert "/tmp/cog-home/.local/state/cog" in containers

    # Verify mount modes — .gitconfig must be ro, others rw
    by_container = {p.split(":")[1]: p.split(":")[2] for p in pairs}
    assert by_container["/work"] == "rw"
    assert by_container["/tmp/cog-home/.gitconfig"] == "ro"
    assert by_container["/tmp/cog-home/.claude"] == "rw"
    assert by_container["/tmp/cog-home/.local/state/cog"] == "rw"


def test_mount_args_skips_absent_host_paths(tmp_path: Path) -> None:
    home = _make_home(tmp_path, skip={".claude"})
    with (
        patch("cog.runners.docker_sandbox.Path.home", return_value=home),
        patch("cog.runners.docker_sandbox.Path.cwd", return_value=Path("/work")),
    ):
        sandbox = DockerSandbox()
        args = sandbox._mount_args()

    pairs = [args[i + 1] for i in range(0, len(args), 2) if args[i] == "-v"]
    containers = [p.split(":")[1] for p in pairs]
    assert "/tmp/cog-home/.claude" not in containers


async def test_state_dir_mkdir_before_mount(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "key123")
    monkeypatch.setattr(_PATCH_DOCKERFILE, _FAKE_DOCKERFILE)

    state_dir = tmp_path / ".local" / "state" / "cog"
    assert not state_dir.exists()

    with (
        _patch_which(),
        _patch_exec(*_standard_build_procs(image_exists=True)),
        patch("cog.runners.docker_sandbox.Path.home", return_value=tmp_path),
    ):
        sandbox = DockerSandbox()
        await sandbox.prepare()

    assert state_dir.exists()


# ---------------------------------------------------------------------------
# wrap_argv / wrap_env
# ---------------------------------------------------------------------------


def test_wrap_argv_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with (
        patch("cog.runners.docker_sandbox.Path.home", return_value=Path("/fake-home")),
        patch("cog.runners.docker_sandbox.Path.cwd", return_value=Path("/fake-cwd")),
        patch("pathlib.Path.exists", return_value=False),
        patch("os.getuid", return_value=1000),
        patch("os.getgid", return_value=1000),
    ):
        sandbox = DockerSandbox()
        result = sandbox.wrap_argv(["claude", "--help"])

    assert result[:2] == ["docker", "run"]
    assert "--rm" in result
    assert "-w" in result
    assert result[result.index("-w") + 1] == "/work"
    assert "-e" in result
    assert "HOME=/tmp/cog-home" in result
    assert result[-2:] == ["claude", "--help"]
    assert result[-3] == "cog:latest"


def test_wrap_env_minimal() -> None:
    sandbox = DockerSandbox()
    env = {"PATH": "/usr/bin", "HOME": "/root", "USER": "alice", "SECRET": "s3cr3t"}
    result = sandbox.wrap_env(env)
    assert result == {"PATH": "/usr/bin", "HOME": "/root", "USER": "alice"}
    assert "SECRET" not in result


def test_wrap_env_missing_keys() -> None:
    sandbox = DockerSandbox()
    result = sandbox.wrap_env({"PATH": "/usr/bin"})
    assert result == {"PATH": "/usr/bin"}


# ---------------------------------------------------------------------------
# ANTHROPIC_API_KEY passthrough
# ---------------------------------------------------------------------------


async def test_anthropic_api_key_passthrough(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")
    monkeypatch.setattr(_PATCH_DOCKERFILE, _FAKE_DOCKERFILE)

    security_called = False

    async def tracking_exec(*args: str, **kwargs: Any) -> FakeProc:
        nonlocal security_called
        if args[0] == "security":
            security_called = True
        return FakeProc(0)

    with (
        _patch_which(),
        patch(
            "cog.runners.docker_sandbox.asyncio.create_subprocess_exec",
            new=AsyncMock(side_effect=tracking_exec),
        ),
        patch("cog.runners.docker_sandbox.Path.home", return_value=tmp_path),
    ):
        sandbox = DockerSandbox()
        await sandbox.prepare()

    assert not security_called

    with (
        patch("cog.runners.docker_sandbox.Path.home", return_value=tmp_path),
        patch("cog.runners.docker_sandbox.Path.cwd", return_value=Path("/work")),
        patch("pathlib.Path.exists", return_value=False),
        patch("os.getuid", return_value=1000),
        patch("os.getgid", return_value=1000),
    ):
        argv = sandbox.wrap_argv(["claude"])

    assert "-e" in argv
    key_flags = [argv[i + 1] for i in range(len(argv) - 1) if argv[i] == "-e"]
    assert any("ANTHROPIC_API_KEY=sk-test-key" == f for f in key_flags)


# ---------------------------------------------------------------------------
# Keychain refresh
# ---------------------------------------------------------------------------


async def test_keychain_refresh_writes_credentials_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(_PATCH_DOCKERFILE, _FAKE_DOCKERFILE)

    creds_data = b'{"token": "abc123"}'
    procs = [
        *_standard_build_procs(image_exists=True),
        FakeProc(0, creds_data),  # security
    ]

    with (
        _patch_which(),
        _patch_exec(*procs),
        patch("cog.runners.docker_sandbox.Path.home", return_value=tmp_path),
    ):
        sandbox = DockerSandbox()
        await sandbox.prepare()

    creds_file = tmp_path / ".claude" / ".credentials.json"
    assert creds_file.exists()
    assert creds_file.read_bytes() == creds_data
    assert oct(creds_file.stat().st_mode & 0o777) == oct(0o600)


async def test_keychain_refresh_atomic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Credentials are written via tempfile + os.replace (atomic rename)."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(_PATCH_DOCKERFILE, _FAKE_DOCKERFILE)

    replace_calls: list[tuple[str, str]] = []
    real_replace = os.replace

    def tracking_replace(src: str, dst: str) -> None:
        replace_calls.append((src, dst))
        real_replace(src, dst)

    procs = [*_standard_build_procs(image_exists=True), FakeProc(0, b'{"t":"x"}')]
    with (
        _patch_which(),
        _patch_exec(*procs),
        patch("cog.runners.docker_sandbox.Path.home", return_value=tmp_path),
        patch("cog.runners.docker_sandbox.os.replace", side_effect=tracking_replace),
    ):
        sandbox = DockerSandbox()
        await sandbox.prepare()

    assert len(replace_calls) == 1
    src, dst = replace_calls[0]
    assert Path(dst) == tmp_path / ".claude" / ".credentials.json"
    # src was a temp file in the same dir (atomic same-filesystem rename)
    assert Path(src).parent == tmp_path / ".claude"


async def test_keychain_missing_warns_doesnt_raise(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(_PATCH_DOCKERFILE, _FAKE_DOCKERFILE)

    procs = [*_standard_build_procs(image_exists=True), FakeProc(1)]  # security fails

    with (
        _patch_which(),
        _patch_exec(*procs),
        patch("cog.runners.docker_sandbox.Path.home", return_value=tmp_path),
    ):
        sandbox = DockerSandbox()
        await sandbox.prepare()  # must not raise

    assert "warning" in capsys.readouterr().err.lower()


async def test_keychain_security_binary_missing_warns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(_PATCH_DOCKERFILE, _FAKE_DOCKERFILE)

    procs = _standard_build_procs(image_exists=True)

    with (
        _patch_which(has_security=False),
        _patch_exec(*procs),
        patch("cog.runners.docker_sandbox.Path.home", return_value=tmp_path),
    ):
        sandbox = DockerSandbox()
        await sandbox.prepare()  # must not raise

    assert "warning" in capsys.readouterr().err.lower()


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------


async def test_smoke_test_succeeds() -> None:
    with _patch_exec(FakeProc(0)):
        sandbox = DockerSandbox()
        await sandbox.smoke_test()  # must not raise


async def test_smoke_test_raises_on_failure() -> None:
    with _patch_exec(FakeProc(1)):
        sandbox = DockerSandbox()
        with pytest.raises(SandboxError, match="smoke test failed"):
            await sandbox.smoke_test()


# ---------------------------------------------------------------------------
# Error cases
# ---------------------------------------------------------------------------


async def test_docker_unavailable_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "key")
    monkeypatch.setattr(_PATCH_DOCKERFILE, _FAKE_DOCKERFILE)

    with (
        _patch_which(),
        _patch_exec(FakeProc(1)),  # docker info fails
        patch("cog.runners.docker_sandbox.Path.home", return_value=tmp_path),
    ):
        sandbox = DockerSandbox()
        with pytest.raises(DockerUnavailableError):
            await sandbox.prepare()


async def test_docker_unavailable_raises_when_binary_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "key")
    monkeypatch.setattr(_PATCH_DOCKERFILE, _FAKE_DOCKERFILE)

    with (
        _patch_which(has_docker=False),
        patch("cog.runners.docker_sandbox.Path.home", return_value=tmp_path),
    ):
        sandbox = DockerSandbox()
        with pytest.raises(DockerUnavailableError):
            await sandbox.prepare()


async def test_docker_build_fail_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "key")
    monkeypatch.setattr(_PATCH_DOCKERFILE, _FAKE_DOCKERFILE)

    procs = _standard_build_procs(image_exists=True, build_rc=1)

    with (
        _patch_which(),
        _patch_exec(*procs),
        patch("cog.runners.docker_sandbox.Path.home", return_value=tmp_path),
    ):
        sandbox = DockerSandbox()
        with pytest.raises(DockerImageBuildError):
            await sandbox.prepare()


# ---------------------------------------------------------------------------
# _image_exists label check
# ---------------------------------------------------------------------------


async def test_image_exists_returns_true_when_label_matches_expected_version() -> None:
    with _patch_exec(FakeProc(0, f"{_EXPECTED_IMAGE_VERSION}\n".encode())):
        sandbox = DockerSandbox()
        assert await sandbox._image_exists() is True


async def test_image_exists_returns_false_when_label_mismatches() -> None:
    with _patch_exec(FakeProc(0, b"1\n")):
        sandbox = DockerSandbox()
        assert await sandbox._image_exists() is False


async def test_image_exists_returns_false_when_label_absent() -> None:
    with _patch_exec(FakeProc(0, b"\n")):
        sandbox = DockerSandbox()
        assert await sandbox._image_exists() is False


async def test_image_exists_returns_false_when_tag_not_found() -> None:
    with _patch_exec(FakeProc(1)):
        sandbox = DockerSandbox()
        assert await sandbox._image_exists() is False


async def test_smoke_test_includes_python_version_check() -> None:
    captured: list[tuple[str, ...]] = []

    async def tracking_exec(*args: str, **kwargs: Any) -> FakeProc:
        captured.append(args)
        return FakeProc(0)

    with patch(
        "cog.runners.docker_sandbox.asyncio.create_subprocess_exec",
        new=AsyncMock(side_effect=tracking_exec),
    ):
        sandbox = DockerSandbox()
        await sandbox.smoke_test()

    shell_cmd = captured[0][-1]
    assert "sys.version_info >= (3, 12)" in shell_cmd
