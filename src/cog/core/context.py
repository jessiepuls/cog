from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from cog.core.item import Item
from cog.core.state import StateCache

if TYPE_CHECKING:
    from cog.telemetry import TelemetryWriter


@dataclass
class ExecutionContext:
    project_dir: Path
    tmp_dir: Path
    state_cache: StateCache
    headless: bool
    item: Item | None = None
    work_branch: str | None = None
    telemetry: TelemetryWriter | None = None
