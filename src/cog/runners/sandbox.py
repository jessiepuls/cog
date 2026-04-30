from collections.abc import Mapping, Sequence
from pathlib import Path


class NullSandbox:
    """Sandbox impl that does nothing. Used by tests and by future debug modes."""

    async def prepare(self) -> None:
        return

    def wrap_argv(self, argv: Sequence[str], cwd: Path | None = None) -> list[str]:
        return list(argv)

    def wrap_env(self, env: Mapping[str, str]) -> dict[str, str]:
        return dict(env)
