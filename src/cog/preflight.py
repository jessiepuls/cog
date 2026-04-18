"""Preflight checks. Full implementation in #7."""

from dataclasses import dataclass
from pathlib import Path
from typing import Literal


@dataclass(frozen=True)
class PreflightResult:
    name: str
    ok: bool
    level: Literal["error", "warning", "info"]
    message: str


async def run_checks(checks: list, project_dir: Path) -> list[PreflightResult]:
    """Run all preflight checks. Stub: full implementation in #7."""
    return []


def print_results(results: list[PreflightResult]) -> None:
    """Print preflight results to stdout. Stub: full implementation in #7."""
    for r in results:
        prefix = "✓" if r.ok else "✗"
        print(f"{prefix} {r.name}: {r.message}")
