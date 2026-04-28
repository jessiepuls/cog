from pathlib import Path

import pytest

from cog.core.errors import TrackerError
from cog.trackers.github import GitHubIssueTracker
from tests.fakes import FakeSubprocessRegistry
from tests.test_trackers.conftest import register_repo

LIST_FIELDS = "number,title,body,labels,assignees,state,createdAt,updatedAt,url"
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


def make_tracker(
    registry: FakeSubprocessRegistry,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> GitHubIssueTracker:
    monkeypatch.setattr("asyncio.create_subprocess_exec", registry.create_subprocess_exec)
    return GitHubIssueTracker(tmp_path)


async def test_nonzero_exit_raises_tracker_error(
    registry: FakeSubprocessRegistry, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # list_by_label calls the list endpoint first; simulate failure there
    registry.expect(
        LIST_ARGV,
        stdout=b"",
        stderr=b"auth failed",
        returncode=1,
    )
    tracker = make_tracker(registry, tmp_path, monkeypatch)
    with pytest.raises(TrackerError, match="auth failed"):
        await tracker.list_by_label("agent-ready")


async def test_malformed_json_raises_tracker_error(
    registry: FakeSubprocessRegistry, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    register_repo(registry)
    registry.expect(LIST_ARGV, stdout=b"not valid json {{{{")
    tracker = make_tracker(registry, tmp_path, monkeypatch)
    with pytest.raises(TrackerError, match="unparseable JSON"):
        await tracker.list_by_label("agent-ready")


async def test_unexpected_argv_raises_test_failure(
    registry: FakeSubprocessRegistry, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FakeSubprocessRegistry raises AssertionError for unexpected calls."""
    monkeypatch.setattr("asyncio.create_subprocess_exec", registry.create_subprocess_exec)
    tracker = GitHubIssueTracker(tmp_path)
    # No expectations registered — first call should raise
    with pytest.raises(AssertionError, match="Unexpected subprocess call"):
        await tracker.list_by_label("agent-ready")
