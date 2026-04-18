from datetime import UTC, datetime
from pathlib import Path

import pytest

from cog.core.item import Item
from cog.trackers.github import GitHubIssueTracker
from tests.fakes import FakeSubprocessRegistry

DUMMY_ITEM = Item(
    tracker_id="github/jessiepuls/cog",
    item_id="42",
    title="Test issue",
    body="Body text",
    labels=(),
    comments=(),
    updated_at=datetime(2026, 4, 18, tzinfo=UTC),
    url="https://github.com/jessiepuls/cog/issues/42",
)


def make_tracker(
    registry: FakeSubprocessRegistry,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> GitHubIssueTracker:
    monkeypatch.setattr("asyncio.create_subprocess_exec", registry.create_subprocess_exec)
    return GitHubIssueTracker(tmp_path)


async def test_comment_argv(
    registry: FakeSubprocessRegistry, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry.expect(("gh", "issue", "comment", "42", "--body", "hello world"))
    tracker = make_tracker(registry, tmp_path, monkeypatch)
    await tracker.comment(DUMMY_ITEM, "hello world")
    assert ("gh", "issue", "comment", "42", "--body", "hello world") in registry.calls


async def test_add_label_argv(
    registry: FakeSubprocessRegistry, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry.expect(("gh", "issue", "edit", "42", "--add-label", "in-progress"))
    tracker = make_tracker(registry, tmp_path, monkeypatch)
    await tracker.add_label(DUMMY_ITEM, "in-progress")
    assert ("gh", "issue", "edit", "42", "--add-label", "in-progress") in registry.calls


async def test_remove_label_argv(
    registry: FakeSubprocessRegistry, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry.expect(("gh", "issue", "edit", "42", "--remove-label", "agent-ready"))
    tracker = make_tracker(registry, tmp_path, monkeypatch)
    await tracker.remove_label(DUMMY_ITEM, "agent-ready")
    assert ("gh", "issue", "edit", "42", "--remove-label", "agent-ready") in registry.calls


async def test_update_body_via_stdin(
    registry: FakeSubprocessRegistry, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry.expect(("gh", "issue", "edit", "42", "--body-file", "-"))
    tracker = make_tracker(registry, tmp_path, monkeypatch)
    await tracker.update_body(DUMMY_ITEM, "new body text")
    call = next(c for c in registry.calls if "edit" in c and "--body-file" in c)
    assert "--body-file" in call
    assert "-" in call
    assert "--title" not in call
    proc = registry._procs[-1]
    assert proc.received_stdin == b"new body text"


async def test_update_body_with_title(
    registry: FakeSubprocessRegistry, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry.expect(("gh", "issue", "edit", "42", "--body-file", "-", "--title", "New Title"))
    tracker = make_tracker(registry, tmp_path, monkeypatch)
    await tracker.update_body(DUMMY_ITEM, "new body text", title="New Title")
    call = next(c for c in registry.calls if "edit" in c and "--body-file" in c)
    assert "--title" in call
    assert "New Title" in call
    proc = registry._procs[-1]
    assert proc.received_stdin == b"new body text"
