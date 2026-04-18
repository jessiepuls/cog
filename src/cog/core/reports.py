from pathlib import Path
from typing import Literal, Protocol

from cog.core.context import ExecutionContext
from cog.core.outcomes import StageResult


class ReportWriter(Protocol):
    """Writes a markdown report for one iteration. Returns the written path, or None if
    the workflow chose not to write one."""

    async def write(
        self,
        ctx: ExecutionContext,
        results: list[StageResult],
        outcome: Literal["success", "noop", "error"],
        *,
        error: Exception | None = None,
    ) -> Path | None: ...
