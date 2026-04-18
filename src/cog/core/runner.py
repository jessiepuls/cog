from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RunResult:
    final_message: str
    total_cost_usd: float
    exit_status: int
    stream_json_path: Path
    duration_seconds: float


class AgentRunner(ABC):
    @abstractmethod
    async def run(self, prompt: str, *, model: str) -> RunResult: ...
