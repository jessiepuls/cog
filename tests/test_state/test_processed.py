from datetime import UTC, datetime, timedelta

from .conftest import make_item


def test_mark_and_is_processed(cache):
    item = make_item()
    cache.mark_processed(item, outcome="success")
    assert cache.is_processed(item)


def test_is_processed_false_for_unknown_item(cache):
    item = make_item()
    assert not cache.is_processed(item)


def test_revival_rule(cache):
    """item.updated_at > processed.ts → is_processed returns False."""
    item = make_item()
    cache.mark_processed(item, outcome="success")
    # Simulate user editing item after it was processed
    updated_item = make_item(updated_at=datetime.now(UTC) + timedelta(hours=1))
    assert not cache.is_processed(updated_item)


def test_mark_processed_overwrites_previous_record(cache):
    item = make_item()
    cache.mark_processed(item, outcome="noop")
    cache.mark_processed(item, outcome="success")
    # Still processed; outcome updated (verified via serialization)
    assert cache.is_processed(item)
    data = cache._serialize()
    assert data["processed_items"][0]["outcome"] == "success"


def test_keyed_by_tracker_id_and_item_id(cache):
    """Same item_id in different trackers are independent records."""
    item_github = make_item(tracker_id="github/org/repo", item_id="1")
    item_jira = make_item(tracker_id="jira/org/proj", item_id="1")
    cache.mark_processed(item_github, outcome="success")
    assert cache.is_processed(item_github)
    assert not cache.is_processed(item_jira)
