from datetime import datetime

from .conftest import make_item


def test_processed_record_json_shape(cache):
    item = make_item()
    cache.mark_processed(item, outcome="success")
    data = cache._serialize()
    rec = data["processed_items"][0]
    assert set(rec.keys()) == {"tracker_id", "item_id", "outcome", "ts"}
    assert rec["tracker_id"] == item.tracker_id
    assert rec["item_id"] == item.item_id
    assert rec["outcome"] == "success"
    assert isinstance(rec["ts"], str)


def test_deferred_record_json_shape(cache):
    item = make_item()
    cache.mark_deferred(item, reason="blocker", blockers=["10", "11"])
    data = cache._serialize()
    rec = data["deferred_items"][0]
    assert set(rec.keys()) == {"tracker_id", "item_id", "reason", "blockers", "ts"}
    assert rec["reason"] == "blocker"
    assert rec["blockers"] == ["10", "11"]


def test_ts_isoformat_utc_aware(cache):
    """Timestamps survive a round-trip through isoformat / fromisoformat."""
    item = make_item()
    cache.mark_processed(item, outcome="success")
    data = cache._serialize()
    ts_str = data["processed_items"][0]["ts"]
    recovered = datetime.fromisoformat(ts_str)
    assert recovered.tzinfo is not None
    assert recovered.utcoffset().total_seconds() == 0
