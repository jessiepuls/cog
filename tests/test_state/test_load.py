import json
from datetime import UTC, datetime
from pathlib import Path

from cog.state import JsonFileStateCache

from .conftest import make_item


def write_state(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def test_load_missing_file_is_empty_not_corrupt(tmp_path):
    cache = JsonFileStateCache(tmp_path / "nonexistent" / "state.json")
    cache.load()
    assert not cache.was_corrupt()
    assert cache.is_empty()


def test_load_happy_path(tmp_path):
    path = tmp_path / "state.json"
    ts = "2024-06-01T10:00:00+00:00"
    write_state(
        path,
        {
            "schema_version": 1,
            "processed_items": [
                {"tracker_id": "github/org/repo", "item_id": "1", "outcome": "success", "ts": ts}
            ],
            "deferred_items": [
                {
                    "tracker_id": "github/org/repo",
                    "item_id": "2",
                    "reason": "blocker",
                    "blockers": ["3"],
                    "ts": ts,
                }
            ],
            "last_run": ts,
        },
    )
    cache = JsonFileStateCache(path)
    cache.load()

    assert not cache.was_corrupt()
    item1 = make_item(item_id="1", updated_at=datetime(2024, 1, 1, tzinfo=UTC))
    item2 = make_item(item_id="2", updated_at=datetime(2024, 1, 1, tzinfo=UTC))
    assert cache.is_processed(item1)
    assert cache.is_deferred(item2)


def test_load_corrupt_json_marks_corrupt(tmp_path, capsys):
    path = tmp_path / "state.json"
    path.write_text("not valid json", encoding="utf-8")
    cache = JsonFileStateCache(path)
    cache.load()
    assert cache.was_corrupt()
    assert cache.is_empty()
    assert "corrupt" in capsys.readouterr().err


def test_load_wrong_schema_version_marks_corrupt(tmp_path, capsys):
    path = tmp_path / "state.json"
    write_state(path, {"schema_version": 2, "processed_items": [], "deferred_items": []})
    cache = JsonFileStateCache(path)
    cache.load()
    assert cache.was_corrupt()
    assert cache.is_empty()
    assert "corrupt" in capsys.readouterr().err


def test_load_missing_schema_version_marks_corrupt(tmp_path, capsys):
    path = tmp_path / "state.json"
    write_state(path, {"processed_items": [], "deferred_items": []})
    cache = JsonFileStateCache(path)
    cache.load()
    assert cache.was_corrupt()
    assert "corrupt" in capsys.readouterr().err
