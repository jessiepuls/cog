from datetime import UTC, datetime
from pathlib import Path

import pytest

from cog.trackers.github import GitHubIssueTracker
from tests.fakes import FakeSubprocessRegistry
from tests.test_trackers.conftest import register_repo

GET_FIELDS = "number,title,body,labels,comments,state,createdAt,updatedAt,url"

SINGLE_ISSUE_WITH_Z = b"""{
  "number": 99,
  "title": "Date test issue",
  "body": "body",
  "labels": [],
  "state": "OPEN",
  "createdAt": "2026-04-18T17:55:06Z",
  "updatedAt": "2026-04-18T17:55:06Z",
  "url": "https://github.com/jessiepuls/cog/issues/99",
  "comments": [
    {
      "author": {"login": "tester"},
      "body": "a comment",
      "createdAt": "2026-04-18T17:55:06Z"
    }
  ]
}"""


async def test_iso_z_suffix_parses_to_utc_aware(
    registry: FakeSubprocessRegistry, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Z-suffixed ISO timestamps must be parsed as tz-aware UTC datetimes."""
    register_repo(registry)
    registry.expect(
        ("gh", "issue", "view", "99", "--json", GET_FIELDS),
        stdout=SINGLE_ISSUE_WITH_Z,
    )
    monkeypatch.setattr("asyncio.create_subprocess_exec", registry.create_subprocess_exec)
    tracker = GitHubIssueTracker(tmp_path)
    item = await tracker.get("99")

    expected = datetime(2026, 4, 18, 17, 55, 6, tzinfo=UTC)
    assert item.updated_at == expected
    assert item.updated_at.tzinfo is not None
    assert item.comments[0].created_at == expected
    assert item.comments[0].created_at.tzinfo is not None


async def test_item_created_at_populated_from_gh_json(
    registry: FakeSubprocessRegistry, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """created_at must be parsed from createdAt in the JSON response."""
    register_repo(registry)
    registry.expect(
        ("gh", "issue", "view", "99", "--json", GET_FIELDS),
        stdout=SINGLE_ISSUE_WITH_Z,
    )
    monkeypatch.setattr("asyncio.create_subprocess_exec", registry.create_subprocess_exec)
    tracker = GitHubIssueTracker(tmp_path)
    item = await tracker.get("99")

    expected = datetime(2026, 4, 18, 17, 55, 6, tzinfo=UTC)
    assert item.created_at == expected
    assert item.created_at.tzinfo is not None
