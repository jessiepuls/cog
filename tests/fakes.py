from collections.abc import AsyncIterator
from pathlib import Path

from cog.core.item import Item
from cog.core.runner import AgentRunner, ResultEvent, RunEvent, RunResult


class EchoRunner(AgentRunner):
    """Returns the prompt as the final_message. Zero cost, zero duration."""

    async def stream(self, prompt: str, *, model: str) -> AsyncIterator[RunEvent]:
        yield ResultEvent(
            result=RunResult(
                final_message=prompt,
                total_cost_usd=0.0,
                exit_status=0,
                stream_json_path=Path("/dev/null"),
                duration_seconds=0.0,
            )
        )


class InMemoryStateCache:
    """Dict-backed StateCache. Structurally satisfies the StateCache Protocol."""

    def __init__(self) -> None:
        self._processed: dict[str, str] = {}
        self._deferred: dict[str, dict[str, object]] = {}

    def _key(self, item: Item) -> str:
        return f"{item.tracker_id}:{item.item_id}"

    def is_processed(self, item: Item) -> bool:
        return self._key(item) in self._processed

    def mark_processed(self, item: Item, outcome: str) -> None:
        self._processed[self._key(item)] = outcome

    def is_deferred(self, item: Item) -> bool:
        return self._key(item) in self._deferred

    def mark_deferred(self, item: Item, reason: str, blockers: list[str]) -> None:
        self._deferred[self._key(item)] = {"reason": reason, "blockers": blockers}

    def clear_deferral(self, item: Item) -> None:
        self._deferred.pop(self._key(item), None)

    def save(self) -> None:
        pass
