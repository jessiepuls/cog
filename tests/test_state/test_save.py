import fcntl
import json
from unittest.mock import patch

import pytest

from cog.state import JsonFileStateCache

from .conftest import make_item


def test_save_writes_file_and_creates_parent_dir(tmp_path):
    path = tmp_path / "nested" / "dir" / "state.json"
    cache = JsonFileStateCache(path)
    cache.save()
    assert path.exists()


def test_save_json_shape(tmp_path):
    path = tmp_path / "state.json"
    cache = JsonFileStateCache(path)
    item = make_item(item_id="1")
    cache.mark_processed(item, outcome="success")
    deferred = make_item(item_id="2")
    cache.mark_deferred(deferred, reason="blocker", blockers=["3"])
    cache.save()

    data = json.loads(path.read_text())
    assert data["schema_version"] == 1
    assert len(data["processed_items"]) == 1
    assert len(data["deferred_items"]) == 1
    assert data["last_run"] is not None


def test_save_sorted_and_indented(tmp_path):
    path = tmp_path / "state.json"
    cache = JsonFileStateCache(path)
    # Add items out of order
    cache.mark_processed(make_item(item_id="20"), outcome="success")
    cache.mark_processed(make_item(item_id="5"), outcome="noop")
    cache.save()

    text = path.read_text()
    # Indented (not compact)
    assert "\n" in text
    data = json.loads(text)
    ids = [r["item_id"] for r in data["processed_items"]]
    assert ids == sorted(ids)


def test_save_acquires_flock(tmp_path):
    path = tmp_path / "state.json"
    cache = JsonFileStateCache(path)
    with patch("fcntl.flock") as mock_flock:
        cache.save()
    mock_flock.assert_called_once()
    args = mock_flock.call_args[0]
    assert args[1] == fcntl.LOCK_EX


def test_save_tempfile_and_atomic_rename(tmp_path):
    path = tmp_path / "state.json"
    cache = JsonFileStateCache(path)
    with patch("os.replace") as mock_replace:
        cache.save()
    mock_replace.assert_called_once()
    dest = mock_replace.call_args[0][1]
    assert dest == path


def test_save_tempfile_cleanup_on_rename_failure(tmp_path):
    path = tmp_path / "state.json"
    cache = JsonFileStateCache(path)
    with patch("os.replace", side_effect=OSError("disk full")):
        with pytest.raises(OSError, match="disk full"):
            cache.save()
    # No .tmp files should linger
    tmp_files = list(tmp_path.glob("*.json.tmp"))
    assert tmp_files == []


def test_save_raises_on_permission_denied(tmp_path):
    path = tmp_path / "state.json"
    cache = JsonFileStateCache(path)
    # Make parent dir read-only so mkstemp fails
    tmp_path.chmod(0o555)
    try:
        with pytest.raises(OSError):
            cache.save()
    finally:
        tmp_path.chmod(0o755)


def test_save_updates_last_run_timestamp(tmp_path):
    path = tmp_path / "state.json"
    cache = JsonFileStateCache(path)
    assert cache._last_run is None
    cache.save()
    assert cache._last_run is not None
    data = json.loads(path.read_text())
    assert data["last_run"] is not None


def test_load_after_save_roundtrip(tmp_path):
    path = tmp_path / "state.json"
    cache = JsonFileStateCache(path)
    item1 = make_item(item_id="1")
    item2 = make_item(item_id="2")
    cache.mark_processed(item1, outcome="success")
    cache.mark_deferred(item2, reason="blocker", blockers=["3", "4"])
    cache.save()

    cache2 = JsonFileStateCache(path)
    cache2.load()
    assert not cache2.was_corrupt()
    assert cache2.is_processed(item1)
    assert cache2.is_deferred(item2)
    assert cache2._last_run is not None
