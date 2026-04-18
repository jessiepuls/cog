from pathlib import Path

import pytest

from cog.trackers.github import GitHubIssueTracker
from tests.fakes import FakeSubprocessRegistry
from tests.test_trackers.conftest import load_fixture, register_repo

LIST_FIELDS = "number,title,body,labels,createdAt,updatedAt,url"
LIST_BASE_ARGV = (
    "gh",
    "issue",
    "list",
    "--label",
    "agent-ready",
    "--state",
    "open",
    "--json",
    LIST_FIELDS,
)
REPO_ARGV = ("gh", "repo", "view", "--json", "nameWithOwner")


def make_tracker(registry: FakeSubprocessRegistry, tmp_path: Path) -> GitHubIssueTracker:
    tracker = GitHubIssueTracker(tmp_path)
    tracker._create_subprocess_exec = registry.create_subprocess_exec  # type: ignore[attr-defined]
    return tracker


async def list_by_label(
    registry: FakeSubprocessRegistry,
    tmp_path: Path,
    *,
    label: str = "agent-ready",
    assignee: str | None = None,
    monkeypatch: pytest.MonkeyPatch,
) -> list:
    monkeypatch.setattr("asyncio.create_subprocess_exec", registry.create_subprocess_exec)
    tracker = GitHubIssueTracker(tmp_path)
    return await tracker.list_by_label(label, assignee=assignee)


async def test_list_by_label_happy(
    registry: FakeSubprocessRegistry, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    register_repo(registry)
    registry.expect(LIST_BASE_ARGV, stdout=load_fixture("list_by_label_happy.json"))
    items = await list_by_label(registry, tmp_path, monkeypatch=monkeypatch)

    assert len(items) == 3
    assert items[0].item_id == "10"
    assert items[0].title == "Fix login bug"
    assert items[0].body == "Users can't log in with SSO."
    assert items[0].comments == ()
    assert items[0].tracker_id == "github/jessiepuls/cog"
    assert items[0].url == "https://github.com/jessiepuls/cog/issues/10"
    # all items have empty comments
    assert all(item.comments == () for item in items)


async def test_list_by_label_empty(
    registry: FakeSubprocessRegistry, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    register_repo(registry)
    registry.expect(LIST_BASE_ARGV, stdout=load_fixture("list_by_label_empty.json"))
    items = await list_by_label(registry, tmp_path, monkeypatch=monkeypatch)
    assert items == []


async def test_list_by_label_no_assignee(
    registry: FakeSubprocessRegistry, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    register_repo(registry)
    registry.expect(LIST_BASE_ARGV, stdout=b"[]")
    await list_by_label(registry, tmp_path, assignee=None, monkeypatch=monkeypatch)
    calls = registry.calls
    list_call = next(c for c in calls if "issue" in c and "list" in c)
    assert "--assignee" not in list_call


async def test_list_by_label_with_assignee(
    registry: FakeSubprocessRegistry, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    argv_with_assignee = LIST_BASE_ARGV + ("--assignee", "@me")
    register_repo(registry)
    registry.expect(argv_with_assignee, stdout=b"[]")
    await list_by_label(registry, tmp_path, assignee="@me", monkeypatch=monkeypatch)
    calls = registry.calls
    list_call = next(c for c in calls if "issue" in c and "list" in c)
    assert "--assignee" in list_call
    assert "@me" in list_call


async def test_list_by_label_json_fields(
    registry: FakeSubprocessRegistry, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    register_repo(registry)
    registry.expect(LIST_BASE_ARGV, stdout=b"[]")
    await list_by_label(registry, tmp_path, monkeypatch=monkeypatch)
    calls = registry.calls
    list_call = next(c for c in calls if "issue" in c and "list" in c)
    json_idx = list(list_call).index("--json")
    field_str = list_call[json_idx + 1]
    fields = set(field_str.split(","))
    assert fields == {"number", "title", "body", "labels", "createdAt", "updatedAt", "url"}


async def test_list_by_label_label_normalization(
    registry: FakeSubprocessRegistry, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Labels come in as {name, color, description} objects; Item.labels is tuple of strings."""
    register_repo(registry)
    registry.expect(LIST_BASE_ARGV, stdout=load_fixture("list_by_label_happy.json"))
    items = await list_by_label(registry, tmp_path, monkeypatch=monkeypatch)
    for item in items:
        assert isinstance(item.labels, tuple)
        assert all(isinstance(lbl, str) for lbl in item.labels)
    assert "agent-ready" in items[0].labels
    assert "bug" in items[0].labels
