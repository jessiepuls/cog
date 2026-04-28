from pathlib import Path

import pytest

from cog.trackers.github import GitHubIssueTracker
from tests.fakes import FakeSubprocessRegistry
from tests.test_trackers.conftest import load_fixture, register_repo

GET_FIELDS = "number,title,body,labels,assignees,comments,state,createdAt,updatedAt,url"


async def get_item(
    registry: FakeSubprocessRegistry,
    tmp_path: Path,
    item_id: str,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr("asyncio.create_subprocess_exec", registry.create_subprocess_exec)
    tracker = GitHubIssueTracker(tmp_path)
    return await tracker.get(item_id)


async def test_get_with_comments(
    registry: FakeSubprocessRegistry, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    register_repo(registry)
    registry.expect(
        ("gh", "issue", "view", "10", "--json", GET_FIELDS),
        stdout=load_fixture("get_with_comments.json"),
    )
    item = await get_item(registry, tmp_path, "10", monkeypatch)

    assert len(item.comments) == 2
    assert item.comments[0].author == "alice"
    assert item.comments[0].body == "I can reproduce this with Chrome on macOS."
    assert item.comments[0].created_at.tzinfo is not None
    assert item.comments[0].created_at.utcoffset().total_seconds() == 0  # type: ignore[union-attr]
    assert item.comments[1].author == "bob"


async def test_get_empty_comments(
    registry: FakeSubprocessRegistry, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    register_repo(registry)
    registry.expect(
        ("gh", "issue", "view", "11", "--json", GET_FIELDS),
        stdout=load_fixture("get_no_comments.json"),
    )
    item = await get_item(registry, tmp_path, "11", monkeypatch)
    assert item.comments == ()


async def test_get_json_fields(
    registry: FakeSubprocessRegistry, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    register_repo(registry)
    registry.expect(
        ("gh", "issue", "view", "10", "--json", GET_FIELDS),
        stdout=load_fixture("get_with_comments.json"),
    )
    await get_item(registry, tmp_path, "10", monkeypatch)
    calls = registry.calls
    view_call = next(c for c in calls if "issue" in c and "view" in c)
    json_idx = list(view_call).index("--json")
    fields = set(view_call[json_idx + 1].split(","))
    assert "comments" in fields
    assert "state" in fields
