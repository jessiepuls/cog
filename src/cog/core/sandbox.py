from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Protocol


class Sandbox(Protocol):
    """Prepares and wraps subprocess invocations so they run in the chosen environment."""

    async def prepare(self) -> None:
        """Host-side setup: build/refresh image, refresh keychain creds, etc.
        Called before each wrap_* cycle. Implementations may cache internally."""
        ...

    def wrap_argv(self, argv: Sequence[str], cwd: Path | None = None) -> list[str]:
        """Transform claude's argv into the argv actually passed to create_subprocess_exec.

        `cwd` is the host-absolute working directory for the subprocess (the active
        worktree path for ralph stages). DockerSandbox maps it to a container path
        via `--workdir`; NullSandbox ignores it."""
        ...

    def wrap_env(self, env: Mapping[str, str]) -> dict[str, str]:
        """Transform env vars passed through to the subprocess."""
        ...
