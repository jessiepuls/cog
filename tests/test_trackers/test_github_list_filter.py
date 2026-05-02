"""Tests for GitHubIssueTracker.list(ItemListFilter)."""

from pathlib import Path

import pytest

from cog.core.tracker import ItemListFilter
from cog.trackers.github import GitHubIssueTracker
from tests.fakes import FakeSubprocessRegistry
from tests.test_trackers.conftest import load_fixture, register_repo

LIST_FIELDS = "number,title,body,labels,assignees,state,createdAt,updatedAt,url"


def _base_argv(
    state: str = "open",
    limit: str = "1000",
    *extra: str,
) -> tuple[str, ...]:
    return (
        "gh",
        "issue",
        "list",
        "--state",
        state,
        "--limit",
        limit,
        "--json",
        LIST_FIELDS,
        *extra,
    )


async def _call_list(
    registry: FakeSubprocessRegistry,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    filter: ItemListFilter | None = None,
) -> list:
    monkeypatch.setattr("asyncio.create_subprocess_exec", registry.create_subprocess_exec)
    tracker = GitHubIssueTracker(tmp_path)
    return await tracker.list(filter)


async def test_list_default_filter_uses_open_and_limit_1000(
    registry: FakeSubprocessRegistry, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    register_repo(registry)
    registry.expect(_base_argv(), stdout=b"[]")
    await _call_list(registry, tmp_path, monkeypatch)
    list_call = next(c for c in registry.calls if "issue" in c and "list" in c)
    assert "--state" in list_call
    assert "open" in list_call
    assert "--limit" in list_call
    assert "1000" in list_call


async def test_list_none_filter_same_as_default(
    registry: FakeSubprocessRegistry, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    register_repo(registry)
    registry.expect(_base_argv(), stdout=b"[]")
    await _call_list(registry, tmp_path, monkeypatch, filter=None)
    list_call = next(c for c in registry.calls if "issue" in c and "list" in c)
    assert "--state" in list_call


async def test_list_state_all(
    registry: FakeSubprocessRegistry, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    register_repo(registry)
    registry.expect(_base_argv(state="all"), stdout=b"[]")
    await _call_list(registry, tmp_path, monkeypatch, filter=ItemListFilter(state="all"))
    list_call = next(c for c in registry.calls if "issue" in c and "list" in c)
    assert "all" in list_call


async def test_list_state_closed(
    registry: FakeSubprocessRegistry, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    register_repo(registry)
    registry.expect(_base_argv(state="closed"), stdout=b"[]")
    await _call_list(registry, tmp_path, monkeypatch, filter=ItemListFilter(state="closed"))
    list_call = next(c for c in registry.calls if "issue" in c and "list" in c)
    assert "closed" in list_call


async def test_list_single_label(
    registry: FakeSubprocessRegistry, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    register_repo(registry)
    registry.expect(
        _base_argv() + ("--label", "bug"),
        stdout=b"[]",
    )
    await _call_list(registry, tmp_path, monkeypatch, filter=ItemListFilter(labels=("bug",)))
    list_call = next(c for c in registry.calls if "issue" in c and "list" in c)
    assert "--label" in list_call
    assert "bug" in list_call


async def test_list_multiple_labels_repeated_flag(
    registry: FakeSubprocessRegistry, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    register_repo(registry)
    registry.expect(
        _base_argv() + ("--label", "bug", "--label", "needs-refinement"),
        stdout=b"[]",
    )
    await _call_list(
        registry,
        tmp_path,
        monkeypatch,
        filter=ItemListFilter(labels=("bug", "needs-refinement")),
    )
    list_call = next(c for c in registry.calls if "issue" in c and "list" in c)
    label_indices = [i for i, t in enumerate(list_call) if t == "--label"]
    assert len(label_indices) == 2


async def test_list_no_label_omits_label_flag(
    registry: FakeSubprocessRegistry, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    register_repo(registry)
    registry.expect(_base_argv(), stdout=b"[]")
    await _call_list(registry, tmp_path, monkeypatch, filter=ItemListFilter())
    list_call = next(c for c in registry.calls if "issue" in c and "list" in c)
    assert "--label" not in list_call


async def test_list_assignee(
    registry: FakeSubprocessRegistry, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    register_repo(registry)
    registry.expect(_base_argv() + ("--assignee", "@me"), stdout=b"[]")
    await _call_list(registry, tmp_path, monkeypatch, filter=ItemListFilter(assignee="@me"))
    list_call = next(c for c in registry.calls if "issue" in c and "list" in c)
    assert "--assignee" in list_call
    assert "@me" in list_call


async def test_list_no_assignee_omits_flag(
    registry: FakeSubprocessRegistry, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    register_repo(registry)
    registry.expect(_base_argv(), stdout=b"[]")
    await _call_list(registry, tmp_path, monkeypatch, filter=ItemListFilter(assignee=None))
    list_call = next(c for c in registry.calls if "issue" in c and "list" in c)
    assert "--assignee" not in list_call


async def test_list_search(
    registry: FakeSubprocessRegistry, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    register_repo(registry)
    registry.expect(_base_argv() + ("--search", "login bug"), stdout=b"[]")
    await _call_list(registry, tmp_path, monkeypatch, filter=ItemListFilter(search="login bug"))
    list_call = next(c for c in registry.calls if "issue" in c and "list" in c)
    assert "--search" in list_call
    assert "login bug" in list_call


async def test_list_no_search_omits_flag(
    registry: FakeSubprocessRegistry, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    register_repo(registry)
    registry.expect(_base_argv(), stdout=b"[]")
    await _call_list(registry, tmp_path, monkeypatch, filter=ItemListFilter(search=None))
    list_call = next(c for c in registry.calls if "issue" in c and "list" in c)
    assert "--search" not in list_call


async def test_list_custom_limit(
    registry: FakeSubprocessRegistry, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    register_repo(registry)
    registry.expect(_base_argv(limit="500"), stdout=b"[]")
    await _call_list(registry, tmp_path, monkeypatch, filter=ItemListFilter(limit=500))
    list_call = next(c for c in registry.calls if "issue" in c and "list" in c)
    assert "500" in list_call


async def test_list_returns_items(
    registry: FakeSubprocessRegistry, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    register_repo(registry)
    registry.expect(
        _base_argv(state="all"),
        stdout=load_fixture("list_by_label_happy.json"),
    )
    items = await _call_list(registry, tmp_path, monkeypatch, filter=ItemListFilter(state="all"))
    assert len(items) == 3
    assert all(item.comments == () for item in items)


async def test_list_all_flags_combined(
    registry: FakeSubprocessRegistry, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    register_repo(registry)
    registry.expect(
        _base_argv(state="all", limit="50")
        + ("--label", "bug", "--assignee", "alice", "--search", "crash"),
        stdout=b"[]",
    )
    await _call_list(
        registry,
        tmp_path,
        monkeypatch,
        filter=ItemListFilter(
            labels=("bug",),
            state="all",
            assignee="alice",
            search="crash",
            limit=50,
        ),
    )
    list_call = next(c for c in registry.calls if "issue" in c and "list" in c)
    assert "--label" in list_call
    assert "--assignee" in list_call
    assert "--search" in list_call
    assert "all" in list_call
    assert "50" in list_call
