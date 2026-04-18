from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from cog.core.stage import Stage

Outcome = Literal["success", "noop"]


@dataclass(frozen=True)
class StageResult:
    stage: Stage
    duration_seconds: float
    cost_usd: float
    exit_status: int
    final_message: str
    stream_json_path: Path
    commits_created: int  # executor captures via git rev-list before/after
