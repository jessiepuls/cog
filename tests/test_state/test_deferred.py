from .conftest import make_item


def test_mark_and_is_deferred(cache):
    item = make_item()
    cache.mark_deferred(item, reason="blocker", blockers=["10", "11"])
    assert cache.is_deferred(item)


def test_clear_deferral(cache):
    item = make_item()
    cache.mark_deferred(item, reason="blocker", blockers=["10"])
    cache.clear_deferral(item)
    assert not cache.is_deferred(item)


def test_is_deferred_false_for_unknown_item(cache):
    item = make_item()
    assert not cache.is_deferred(item)


def test_blockers_persist_as_tuple(cache):
    item = make_item()
    cache.mark_deferred(item, reason="blocker", blockers=["10", "11"])
    data = cache._serialize()
    rec = data["deferred_items"][0]
    assert rec["blockers"] == ["10", "11"]
    # Internal storage is a tuple
    key = (item.tracker_id, item.item_id)
    assert isinstance(cache._deferred[key].blockers, tuple)


def test_clear_deferral_noop_for_unknown_item(cache):
    item = make_item()
    # Should not raise
    cache.clear_deferral(item)
    assert not cache.is_deferred(item)
