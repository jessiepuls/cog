import asyncio
import importlib.resources
import os
import shutil
import sys
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path

from cog.core.errors import DockerImageBuildError, DockerUnavailableError, SandboxError


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


class DockerSandbox:
    def __init__(self, image: str = "cog:latest") -> None:
        self._image = image
        self._built = False

    async def prepare(self) -> None:
        state_dir = Path.home() / ".local" / "state" / "cog"
        state_dir.mkdir(parents=True, exist_ok=True)
        if not self._built:
            await self._ensure_image_built()
            self._built = True
        await self._refresh_keychain_credentials()

    def wrap_argv(self, argv: Sequence[str]) -> list[str]:
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
            "/work",
            self._image,
            *argv,
        ]

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
            "claude --version && gh --version && jq --version && uv --version",
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
            self._image,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()
        return proc.returncode == 0

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

    async def _refresh_keychain_credentials(self) -> None:
        if "ANTHROPIC_API_KEY" in os.environ:
            return
        if not shutil.which("security"):
            print(
                "warning: no ANTHROPIC_API_KEY and 'security' binary not found;"
                " skipping keychain refresh",
                file=sys.stderr,
            )
            return
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
            print("warning: Claude Code credentials not found in keychain", file=sys.stderr)
            return
        _write_credentials(stdout)

    def _mount_args(self) -> list[str]:
        home = Path.home()
        cwd = Path.cwd()
        # cwd is always present; other paths mounted only when they exist on the host
        conditional = [
            (home / ".claude", "/tmp/cog-home/.claude", "rw"),
            (home / ".claude.json", "/tmp/cog-home/.claude.json", "rw"),
            (home / ".config" / "gh", "/tmp/cog-home/.config/gh", "rw"),
            (home / ".gitconfig", "/tmp/cog-home/.gitconfig", "ro"),
            (home / ".local" / "state" / "cog", "/tmp/cog-home/.local/state/cog", "rw"),
        ]
        args = ["-v", f"{cwd}:/work:rw"]
        for host, container, mode in conditional:
            if host.exists():
                args.extend(["-v", f"{host}:{container}:{mode}"])
        return args

    def _passthrough_env_args(self) -> list[str]:
        key = os.environ.get("ANTHROPIC_API_KEY")
        return ["-e", f"ANTHROPIC_API_KEY={key}"] if key else []
