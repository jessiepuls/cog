from datetime import UTC, datetime

import pytest

from cog.core.item import Item
from cog.state import JsonFileStateCache


def make_item(
    *,
    tracker_id: str = "github/org/repo",
    item_id: str = "42",
    updated_at: datetime | None = None,
) -> Item:
    return Item(
        tracker_id=tracker_id,
        item_id=item_id,
        title="Test item",
        body="body",
        labels=(),
        comments=(),
        updated_at=updated_at or datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC),
        url="https://github.com/org/repo/issues/42",
    )


@pytest.fixture
def cache(tmp_path) -> JsonFileStateCache:
    return JsonFileStateCache(tmp_path / "state.json")


@pytest.fixture
def item() -> Item:
    return make_item()
