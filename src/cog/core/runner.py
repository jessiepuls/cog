from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from cog.core.item import Item


@dataclass(frozen=True)
class RunResult:
    final_message: str
    total_cost_usd: float
    exit_status: int
    stream_json_path: Path
    duration_seconds: float


@dataclass(frozen=True)
class AssistantTextEvent:
    text: str


@dataclass(frozen=True)
class ToolUseEvent:
    tool: str
    input: Mapping[str, Any]


@dataclass(frozen=True)
class ResultEvent:
    result: RunResult


@dataclass(frozen=True)
class StageStartEvent:
    stage_name: str
    model: str


@dataclass(frozen=True)
class StageEndEvent:
    stage_name: str
    cost_usd: float
    exit_status: int


@dataclass(frozen=True)
class StatusEvent:
    message: str


@dataclass(frozen=True)
class ItemSelectedEvent:
    item: Item


RunEvent = (
    AssistantTextEvent
    | ToolUseEvent
    | ResultEvent
    | StageStartEvent
    | StageEndEvent
    | StatusEvent
    | ItemSelectedEvent
)


class AgentRunner(ABC):
    @abstractmethod
    def stream(
        self, prompt: str, *, model: str, cwd: Path | None = None
    ) -> AsyncIterator[RunEvent]: ...

    async def run(self, prompt: str, *, model: str, cwd: Path | None = None) -> RunResult:
        """Default impl — drain stream() and return the final RunResult."""
        result: RunResult | None = None
        async for event in self.stream(prompt, model=model, cwd=cwd):
            if isinstance(event, ResultEvent):
                result = event.result
        assert result is not None, "runner must emit a ResultEvent before finishing"
        return result
