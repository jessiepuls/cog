import asyncio
import importlib.resources
import json
import os
import shutil
import sys
import tempfile
import time
from collections.abc import Mapping, Sequence
from pathlib import Path

from cog.core.errors import DockerImageBuildError, DockerUnavailableError, SandboxError

_EXPECTED_IMAGE_VERSION = "3"


def _read_bundled_dockerfile() -> bytes:
    resource = importlib.resources.files("cog.resources") / "Dockerfile"
    with resource.open("rb") as f:
        return f.read()


def _write_credentials(data: bytes) -> None:
    creds_dir = Path.home() / ".claude"
    creds_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    dest = creds_dir / ".credentials.json"
    with tempfile.NamedTemporaryFile(mode="wb", dir=creds_dir, delete=False) as tmp:
        tmp.write(data)
        tmp_path = tmp.name
    os.chmod(tmp_path, 0o600)
    os.replace(tmp_path, dest)


def _existing_credentials_still_valid() -> bool:
    creds_file = Path.home() / ".claude" / ".credentials.json"
    try:
        data = json.loads(creds_file.read_bytes())
        expires_at = data["claudeAiOauth"]["expiresAt"]
        if not isinstance(expires_at, int):
            raise TypeError("expiresAt is not an int")
        now_ms = int(time.time() * 1000)
        return expires_at > now_ms + 5 * 60 * 1000
    except Exception:
        return False


async def ensure_credentials(force: bool = False) -> str:
    """Sync keychain credentials to ~/.claude/.credentials.json unless already valid.

    Returns a sentinel string: 'api_key_set', 'skipped', 'security_missing',
    'keychain_missing', or 'refreshed'. Callers map these to appropriate messages.
    """
    if "ANTHROPIC_API_KEY" in os.environ:
        return "api_key_set"
    if not force and _existing_credentials_still_valid():
        return "skipped"
    if not shutil.which("security"):
        return "security_missing"
    print("refreshing Claude Code credentials from keychain", file=sys.stderr)
    proc = await asyncio.create_subprocess_exec(
        "security",
        "find-generic-password",
        "-s",
        "Claude Code-credentials",
        "-w",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    stdout, _ = await proc.communicate()
    if proc.returncode != 0:
        return "keychain_missing"
    _write_credentials(stdout)
    return "refreshed"


class DockerSandbox:
    def __init__(self, image: str = "cog:latest", project_dir: Path | None = None) -> None:
        self._image = image
        self._built = False
        self._project_dir = project_dir

    async def prepare(self) -> None:
        state_dir = Path.home() / ".local" / "state" / "cog"
        state_dir.mkdir(parents=True, exist_ok=True)
        if not self._built:
            await self._ensure_image_built()
            self._built = True
        result = await ensure_credentials()
        if result == "security_missing":
            print(
                "warning: no ANTHROPIC_API_KEY and 'security' binary not found;"
                " skipping keychain refresh",
                file=sys.stderr,
            )
        elif result == "keychain_missing":
            print("warning: Claude Code credentials not found in keychain", file=sys.stderr)

    def wrap_argv(self, argv: Sequence[str], cwd: Path | None = None) -> list[str]:
        workdir = self._container_workdir(cwd)
        return [
            "docker",
            "run",
            "--rm",
            "--user",
            f"{os.getuid()}:{os.getgid()}",
            "-e",
            "HOME=/tmp/cog-home",
            *self._mount_args(),
            *self._passthrough_env_args(),
            "-w",
            workdir,
            self._image,
            *argv,
        ]

    def _container_workdir(self, cwd: Path | None) -> str:
        if cwd is None:
            return "/work"
        project_dir = self._project_dir or Path.cwd()
        try:
            return str(Path("/work") / cwd.relative_to(project_dir))
        except ValueError:
            return "/work"

    def wrap_env(self, env: Mapping[str, str]) -> dict[str, str]:
        return {k: env[k] for k in ("PATH", "HOME", "USER") if k in env}

    async def smoke_test(self) -> None:
        """Verify key tools are present in the image. Raises SandboxError on failure."""
        proc = await asyncio.create_subprocess_exec(
            "docker",
            "run",
            "--rm",
            self._image,
            "sh",
            "-c",
            "claude --version && gh --version && jq --version && uv --version "
            "&& python3 -c 'import sys; assert sys.version_info >= (3, 12), "
            'f"Python 3.12+ required, got {sys.version_info}"\'',
        )
        await proc.wait()
        if proc.returncode != 0:
            raise SandboxError("smoke test failed: one or more tool version checks failed")

    async def _ensure_image_built(self) -> None:
        await self._check_docker_available()
        content = _read_bundled_dockerfile()
        with tempfile.NamedTemporaryFile(suffix=".Dockerfile", delete=False) as f:
            f.write(content)
            tmp_df = f.name
        ctx = tempfile.mkdtemp()
        try:
            exists = await self._image_exists()
            await self._run_build(tmp_df, ctx, quiet=exists)
        finally:
            os.unlink(tmp_df)
            shutil.rmtree(ctx, ignore_errors=True)

    async def _check_docker_available(self) -> None:
        if not shutil.which("docker"):
            raise DockerUnavailableError("docker binary not found on PATH")
        proc = await asyncio.create_subprocess_exec(
            "docker",
            "info",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()
        if proc.returncode != 0:
            raise DockerUnavailableError("docker daemon unreachable (docker info failed)")

    async def _image_exists(self) -> bool:
        proc = await asyncio.create_subprocess_exec(
            "docker",
            "image",
            "inspect",
            "--format",
            '{{ index .Config.Labels "cog.image-version" }}',
            self._image,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await proc.communicate()
        if proc.returncode != 0:
            return False
        return stdout.decode().strip() == _EXPECTED_IMAGE_VERSION

    async def _run_build(self, dockerfile: str, ctx: str, *, quiet: bool) -> None:
        if quiet:
            cmd = ["docker", "build", "--quiet", "-t", self._image, "-f", dockerfile, ctx]
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
        else:
            print("building cog fat image (one-time, ~5 minutes)...", file=sys.stderr)
            cmd = ["docker", "build", "-t", self._image, "-f", dockerfile, ctx]
            proc = await asyncio.create_subprocess_exec(*cmd)
        await proc.wait()
        if proc.returncode != 0:
            raise DockerImageBuildError(f"docker build exited {proc.returncode}")

    def _mount_args(self) -> list[str]:
        home = Path.home()
        project_dir = self._project_dir or Path.cwd()
        # project_dir is always mounted; other paths mounted only when they exist on the host
        conditional = [
            (home / ".claude", "/tmp/cog-home/.claude", "rw"),
            (home / ".claude.json", "/tmp/cog-home/.claude.json", "rw"),
            (home / ".config" / "gh", "/tmp/cog-home/.config/gh", "rw"),
            (home / ".gitconfig", "/tmp/cog-home/.gitconfig", "ro"),
            (home / ".local" / "state" / "cog", "/tmp/cog-home/.local/state/cog", "rw"),
        ]
        args = ["-v", f"{project_dir}:/work:rw"]
        for host, container, mode in conditional:
            if host.exists():
                args.extend(["-v", f"{host}:{container}:{mode}"])
        return args

    def _passthrough_env_args(self) -> list[str]:
        key = os.environ.get("ANTHROPIC_API_KEY")
        return ["-e", f"ANTHROPIC_API_KEY={key}"] if key else []
