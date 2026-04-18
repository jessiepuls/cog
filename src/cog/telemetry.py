import asyncio
import fcntl
import importlib.metadata
import json
import os
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from cog.core.item import Item
from cog.core.outcomes import StageResult

TelemetryOutcome = Literal["success", "no-op", "error", "push-failed", "deferred-by-blocker"]


@dataclass(frozen=True)
class StageTelemetry:
    stage: str
    model: str
    duration_s: float
    cost_usd: float
    exit_status: int
    commits: int
    input_tokens: int = 0
    output_tokens: int = 0

    @classmethod
    def from_stage_result(cls, r: StageResult) -> "StageTelemetry":
        return cls(
            stage=r.stage.name,
            model=r.stage.model,
            duration_s=r.duration_seconds,
            cost_usd=r.cost_usd,
            exit_status=r.exit_status,
            commits=r.commits_created,
        )


@dataclass(frozen=True)
class TelemetryRecord:
    ts: str
    cog_version: str
    project: str
    workflow: str
    item: int
    outcome: TelemetryOutcome
    branch: str | None
    pr_url: str | None
    duration_seconds: float
    stages: tuple[StageTelemetry, ...]
    total_cost_usd: float
    error: str | None

    @classmethod
    def build(
        cls,
        *,
        project: str,
        workflow: str,
        item: Item,
        outcome: TelemetryOutcome,
        results: list[StageResult],
        branch: str | None = None,
        pr_url: str | None = None,
        duration_seconds: float,
        error: str | None = None,
    ) -> "TelemetryRecord":
        return cls(
            ts=datetime.now(UTC).isoformat(),
            cog_version=importlib.metadata.version("cog"),
            project=project,
            workflow=workflow,
            item=int(item.item_id),
            outcome=outcome,
            branch=branch,
            pr_url=pr_url,
            duration_seconds=duration_seconds,
            stages=tuple(StageTelemetry.from_stage_result(r) for r in results),
            total_cost_usd=sum(r.cost_usd for r in results),
            error=error,
        )


class TelemetryWriter:
    def __init__(self, state_dir: Path) -> None:
        self._path = state_dir / "runs.jsonl"

    async def write(self, record: TelemetryRecord) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(asdict(record)) + "\n"
        try:
            await asyncio.to_thread(self._append, line)
        except OSError as e:
            print(f"warning: telemetry write failed: {e}", file=sys.stderr)

    def _append(self, line: str) -> None:
        with self._path.open("a") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            f.write(line)
            f.flush()
            os.fsync(f.fileno())
