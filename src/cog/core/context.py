from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from cog.core.item import Item
from cog.core.sinks import ItemPicker, ReviewProvider, RunEventSink, UserInputProvider
from cog.core.state import StateCache

if TYPE_CHECKING:
    from textual.app import App

    from cog.telemetry import TelemetryWriter


@dataclass
class ExecutionContext:
    project_dir: Path
    tmp_dir: Path
    state_cache: StateCache
    headless: bool
    item: Item | None = None
    work_branch: str | None = None
    worktree_path: Path | None = None
    resumed: bool = False
    event_sink: RunEventSink | None = None
    input_provider: UserInputProvider | None = None
    item_picker: ItemPicker | None = None
    review_provider: ReviewProvider | None = None
    telemetry: TelemetryWriter | None = None
    app: App | None = field(default=None, repr=False)
