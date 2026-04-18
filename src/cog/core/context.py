from dataclasses import dataclass
from pathlib import Path

from cog.core.item import Item
from cog.core.sinks import RunEventSink, UserInputProvider
from cog.core.state import StateCache


@dataclass
class ExecutionContext:
    project_dir: Path
    tmp_dir: Path
    state_cache: StateCache
    headless: bool
    item: Item | None = None
    work_branch: str | None = None
    event_sink: RunEventSink | None = None
    input_provider: UserInputProvider | None = None
