from unittest.mock import AsyncMock

import pytest

from cog.core.host import GitHost
from cog.core.tracker import IssueTracker

from .conftest import make_item


@pytest.fixture
def items():
    return [make_item(item_id=str(i)) for i in range(1, 4)]


@pytest.fixture
def tracker(items):
    mock = AsyncMock(spec=IssueTracker)
    mock.list_by_label.return_value = items
    return mock


@pytest.fixture
def host(items):
    mock = AsyncMock(spec=GitHost)
    # items 1 and 2 have open PRs; item 3 does not
    mock.get_open_prs_mentioning_item.side_effect = lambda item: (
        [object()] if item.item_id in ("1", "2") else []
    )
    return mock


async def test_recover_marks_items_with_open_prs(cache, tracker, host, items):
    await cache.recover_from_remote(tracker, host, queue_label="agent-ready")
    assert cache.is_processed(items[0])
    assert cache.is_processed(items[1])
    assert not cache.is_processed(items[2])


async def test_recover_tolerates_per_item_errors(cache, items, capsys):
    tracker = AsyncMock(spec=IssueTracker)
    tracker.list_by_label.return_value = items
    host = AsyncMock(spec=GitHost)
    # Second item raises; others return a PR
    host.get_open_prs_mentioning_item.side_effect = [
        [object()],
        RuntimeError("network error"),
        [object()],
    ]
    await cache.recover_from_remote(tracker, host, queue_label="agent-ready")
    assert cache.is_processed(items[0])
    assert not cache.is_processed(items[1])
    assert cache.is_processed(items[2])
    assert "warning" in capsys.readouterr().err


async def test_recover_is_idempotent(cache, tracker, host, items):
    await cache.recover_from_remote(tracker, host, queue_label="agent-ready")
    await cache.recover_from_remote(tracker, host, queue_label="agent-ready")
    # Still exactly 2 processed; no duplicates or errors
    data = cache._serialize()
    assert len(data["processed_items"]) == 2


async def test_recover_uses_provided_queue_label(cache, tracker, host):
    await cache.recover_from_remote(tracker, host, queue_label="my-custom-label")
    tracker.list_by_label.assert_called_once_with("my-custom-label", assignee="@me")


async def test_recover_passes_assignee_me(cache, tracker, host):
    await cache.recover_from_remote(tracker, host, queue_label="agent-ready")
    _, kwargs = tracker.list_by_label.call_args
    assert kwargs["assignee"] == "@me"
