"""State persistence layer. Full implementations land in #8/#9."""

from pathlib import Path

from cog.core.item import Item


class JsonFileStateCache:
    """JSON-backed StateCache. Stub: full implementation in #8."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._corrupt = False
        self._empty = True
        self._processed: dict[str, str] = {}
        self._deferred: dict[str, dict[str, object]] = {}

    def load(self) -> None:
        pass

    def was_corrupt(self) -> bool:
        return self._corrupt

    def is_empty(self) -> bool:
        return self._empty

    async def recover_from_remote(self, tracker: object, host: object, label: str) -> None:
        pass

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


def project_state_dir(project_dir: Path) -> Path:
    """Return the .cog state directory for a project. Full implementation in #9."""
    return project_dir / ".cog"


def project_slug(project_dir: Path) -> str:
    """Return a slug identifier for the project. Full implementation in #9."""
    return project_dir.name
