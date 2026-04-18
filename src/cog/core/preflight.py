import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from rich.console import Console


@dataclass(frozen=True)
class PreflightResult:
    check: str
    ok: bool
    level: Literal["error", "warning"]
    message: str


class PreflightCheck(Protocol):
    name: str
    level: Literal["error", "warning"]

    async def run(self, project_dir: Path) -> PreflightResult: ...


async def run_checks(checks: Sequence[PreflightCheck], project_dir: Path) -> list[PreflightResult]:
    """Runs all checks concurrently via asyncio.gather; returns in input order.

    Does NOT short-circuit — users see every problem in one pass.
    """
    return list(await asyncio.gather(*(c.run(project_dir) for c in checks)))


def format_result(result: PreflightResult) -> str:
    """✓ / ✗ / ⚠ prefix + message."""
    if result.ok:
        return f"✓ {result.message}"
    if result.level == "error":
        return f"✗ {result.message}"
    return f"⚠ {result.message}"


def print_results(results: Sequence[PreflightResult], *, _console: Console | None = None) -> None:
    """Writes formatted results to stderr, followed by a summary line."""
    console = _console or Console(stderr=True)
    for result in results:
        console.print(format_result(result))
    console.print(_summary(results))


def _summary(results: Sequence[PreflightResult]) -> str:
    errors = sum(1 for r in results if not r.ok and r.level == "error")
    warnings = sum(1 for r in results if not r.ok and r.level == "warning")
    if errors == 0 and warnings == 0:
        return "Preflight: all checks passed."
    parts = []
    if errors:
        parts.append(f"{errors} error{'s' if errors != 1 else ''}")
    if warnings:
        parts.append(f"{warnings} warning{'s' if warnings != 1 else ''}")
    return f"Preflight: {', '.join(parts)}."
