from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from cog.core.item import Item
from cog.core.state import StateCache

if TYPE_CHECKING:
    from cog.core.runner import RunEvent
    from cog.telemetry import TelemetryWriter


@runtime_checkable
class RunEventSink(Protocol):
    async def emit(self, event: RunEvent) -> None: ...


@dataclass
class ExecutionContext:
    project_dir: Path
    tmp_dir: Path
    state_cache: StateCache
    headless: bool
    item: Item | None = None
    work_branch: str | None = None
    telemetry: TelemetryWriter | None = None
    event_sink: RunEventSink | None = field(default=None, repr=False)
