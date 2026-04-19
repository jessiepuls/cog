from pathlib import Path

import pytest

from cog.trackers.github import GitHubIssueTracker
from tests.fakes import FakeSubprocessRegistry
from tests.test_trackers.conftest import register_repo

LIST_FIELDS = "number,title,body,labels,state,createdAt,updatedAt,url"
LIST_ARGV = (
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


def make_tracker(
    registry: FakeSubprocessRegistry,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> GitHubIssueTracker:
    monkeypatch.setattr("asyncio.create_subprocess_exec", registry.create_subprocess_exec)
    return GitHubIssueTracker(tmp_path)


async def test_tracker_id_lazy_resolved(
    registry: FakeSubprocessRegistry, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """tracker_id should not be resolved until the first list/get call."""
    tracker = make_tracker(registry, tmp_path, monkeypatch)
    assert tracker._tracker_id is None

    register_repo(registry)
    registry.expect(LIST_ARGV, stdout=b"[]")
    await tracker.list_by_label("agent-ready")

    assert tracker._tracker_id == "github/jessiepuls/cog"
    repo_calls = [c for c in registry.calls if c == REPO_ARGV]
    assert len(repo_calls) == 1


async def test_tracker_id_cached_across_calls(
    registry: FakeSubprocessRegistry, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """repo view should only be called once even across multiple list_by_label calls."""
    register_repo(registry)
    registry.expect(LIST_ARGV, stdout=b"[]")

    tracker = make_tracker(registry, tmp_path, monkeypatch)
    await tracker.list_by_label("agent-ready")
    await tracker.list_by_label("agent-ready")

    repo_calls = [c for c in registry.calls if c == REPO_ARGV]
    assert len(repo_calls) == 1


async def test_tracker_id_format(
    registry: FakeSubprocessRegistry, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """tracker_id must be in github/<owner>/<repo> shape."""
    register_repo(registry)
    registry.expect(LIST_ARGV, stdout=b"[]")

    tracker = make_tracker(registry, tmp_path, monkeypatch)
    await tracker.list_by_label("agent-ready")

    parts = tracker._tracker_id.split("/")  # type: ignore[union-attr]
    assert len(parts) == 3
    assert parts[0] == "github"
    assert parts[1] == "jessiepuls"
    assert parts[2] == "cog"
