"""Tests for GitHubIssueTracker.list(ItemListFilter) — search API shape (#200)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cog.core.tracker import ItemListFilter, ItemListResult
from cog.trackers.github import GitHubIssueTracker
from tests.fakes import FakeSubprocessRegistry
from tests.test_trackers.conftest import register_repo

_REPO = "jessiepuls/cog"

_SAMPLE_RECORD = {
    "number": 1,
    "title": "t",
    "body": "b",
    "labels": [],
    "assignees": [],
    "state": "open",
    "created_at": "2026-01-01T00:00:00Z",
    "updated_at": "2026-01-01T00:00:00Z",
    "html_url": "https://github.com/jessiepuls/cog/issues/1",
}

_EMPTY_RESPONSE = json.dumps({"total_count": 0, "incomplete_results": False, "items": []}).encode()


def _search_response(items: list[dict], total: int | None = None) -> bytes:
    return json.dumps(
        {
            "total_count": total if total is not None else len(items),
            "incomplete_results": False,
            "items": items,
        }
    ).encode()


def _search_argv(query: str, page: int = 1) -> tuple[str, ...]:
    return (
        "gh",
        "api",
        "search/issues",
        "-X",
        "GET",
        "-f",
        f"q={query}",
        "-f",
        "sort=updated",
        "-f",
        "order=desc",
        "-f",
        "per_page=100",
        "-f",
        f"page={page}",
    )


async def _call_list(
    registry: FakeSubprocessRegistry,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    filter: ItemListFilter | None = None,
) -> ItemListResult:
    monkeypatch.setattr("asyncio.create_subprocess_exec", registry.create_subprocess_exec)
    tracker = GitHubIssueTracker(tmp_path)
    return await tracker.list(filter)


# ---------------------------------------------------------------------------
# Return type
# ---------------------------------------------------------------------------


async def test_list_returns_item_list_result(
    registry: FakeSubprocessRegistry, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    register_repo(registry)
    registry.expect(
        _search_argv(f"repo:{_REPO} is:issue is:open"),
        stdout=_empty_response_with_total(0),
    )
    result = await _call_list(registry, tmp_path, monkeypatch)
    assert isinstance(result, ItemListResult)


async def test_list_total_comes_from_api(
    registry: FakeSubprocessRegistry, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    register_repo(registry)
    registry.expect(
        _search_argv(f"repo:{_REPO} is:issue is:open"),
        stdout=_search_response([_SAMPLE_RECORD], total=42),
    )
    result = await _call_list(registry, tmp_path, monkeypatch)
    assert result.total == 42
    assert len(result.items) == 1


# ---------------------------------------------------------------------------
# State qualifier
# ---------------------------------------------------------------------------


async def test_list_default_uses_open(
    registry: FakeSubprocessRegistry, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    register_repo(registry)
    registry.expect(
        _search_argv(f"repo:{_REPO} is:issue is:open"),
        stdout=_EMPTY_RESPONSE,
    )
    await _call_list(registry, tmp_path, monkeypatch)
    search_call = _find_search_call(registry)
    assert "is:open" in _query_from_call(search_call)


async def test_list_state_closed(
    registry: FakeSubprocessRegistry, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    register_repo(registry)
    registry.expect(
        _search_argv(f"repo:{_REPO} is:issue is:closed"),
        stdout=_EMPTY_RESPONSE,
    )
    await _call_list(registry, tmp_path, monkeypatch, filter=ItemListFilter(state="closed"))
    query = _query_from_call(_find_search_call(registry))
    assert "is:closed" in query
    assert "is:open" not in query


async def test_list_state_all_omits_is_qualifier(
    registry: FakeSubprocessRegistry, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    register_repo(registry)
    registry.expect(
        _search_argv(f"repo:{_REPO} is:issue"),
        stdout=_EMPTY_RESPONSE,
    )
    await _call_list(registry, tmp_path, monkeypatch, filter=ItemListFilter(state="all"))
    query = _query_from_call(_find_search_call(registry))
    assert "is:open" not in query
    assert "is:closed" not in query


# ---------------------------------------------------------------------------
# Label qualifier
# ---------------------------------------------------------------------------


async def test_list_single_label(
    registry: FakeSubprocessRegistry, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    register_repo(registry)
    registry.expect(
        _search_argv(f'repo:{_REPO} is:issue is:open label:"bug"'),
        stdout=_EMPTY_RESPONSE,
    )
    await _call_list(registry, tmp_path, monkeypatch, filter=ItemListFilter(labels=("bug",)))
    query = _query_from_call(_find_search_call(registry))
    assert 'label:"bug"' in query


async def test_list_multiple_labels(
    registry: FakeSubprocessRegistry, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    register_repo(registry)
    registry.expect(
        _search_argv(f'repo:{_REPO} is:issue is:open label:"bug" label:"docs"'),
        stdout=_EMPTY_RESPONSE,
    )
    await _call_list(
        registry,
        tmp_path,
        monkeypatch,
        filter=ItemListFilter(labels=("bug", "docs")),
    )
    query = _query_from_call(_find_search_call(registry))
    assert 'label:"bug"' in query
    assert 'label:"docs"' in query


async def test_list_no_label_omits_qualifier(
    registry: FakeSubprocessRegistry, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    register_repo(registry)
    registry.expect(
        _search_argv(f"repo:{_REPO} is:issue is:open"),
        stdout=_EMPTY_RESPONSE,
    )
    await _call_list(registry, tmp_path, monkeypatch, filter=ItemListFilter())
    query = _query_from_call(_find_search_call(registry))
    assert "label:" not in query


# ---------------------------------------------------------------------------
# Assignee qualifier
# ---------------------------------------------------------------------------


async def test_list_assignee(
    registry: FakeSubprocessRegistry, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    register_repo(registry)
    registry.expect(
        _search_argv(f"repo:{_REPO} is:issue is:open assignee:alice"),
        stdout=_EMPTY_RESPONSE,
    )
    await _call_list(registry, tmp_path, monkeypatch, filter=ItemListFilter(assignee="alice"))
    query = _query_from_call(_find_search_call(registry))
    assert "assignee:alice" in query


async def test_list_no_assignee_omits_qualifier(
    registry: FakeSubprocessRegistry, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    register_repo(registry)
    registry.expect(
        _search_argv(f"repo:{_REPO} is:issue is:open"),
        stdout=_EMPTY_RESPONSE,
    )
    await _call_list(registry, tmp_path, monkeypatch, filter=ItemListFilter(assignee=None))
    query = _query_from_call(_find_search_call(registry))
    assert "assignee:" not in query


# ---------------------------------------------------------------------------
# Search term
# ---------------------------------------------------------------------------


async def test_list_search_term(
    registry: FakeSubprocessRegistry, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    register_repo(registry)
    registry.expect(
        _search_argv(f"repo:{_REPO} is:issue is:open login bug"),
        stdout=_EMPTY_RESPONSE,
    )
    await _call_list(registry, tmp_path, monkeypatch, filter=ItemListFilter(search="login bug"))
    query = _query_from_call(_find_search_call(registry))
    assert "login bug" in query


# ---------------------------------------------------------------------------
# Item mapping
# ---------------------------------------------------------------------------


async def test_list_maps_search_fields(
    registry: FakeSubprocessRegistry, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    record = {
        "number": 99,
        "title": "Test issue",
        "body": "some body",
        "labels": [{"name": "bug", "color": "ee0701"}],
        "assignees": [{"login": "alice"}],
        "state": "open",
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-02-01T00:00:00Z",
        "html_url": "https://github.com/jessiepuls/cog/issues/99",
    }
    register_repo(registry)
    registry.expect(
        _search_argv(f"repo:{_REPO} is:issue is:open"),
        stdout=_search_response([record]),
    )
    result = await _call_list(registry, tmp_path, monkeypatch)
    assert len(result.items) == 1
    item = result.items[0]
    assert item.item_id == "99"
    assert item.title == "Test issue"
    assert item.labels == ("bug",)
    assert item.assignees == ("alice",)
    assert item.state == "open"
    assert item.comments == ()


async def test_list_maps_closed_state(
    registry: FakeSubprocessRegistry, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    record = {**_SAMPLE_RECORD, "state": "closed"}
    register_repo(registry)
    registry.expect(
        _search_argv(f"repo:{_REPO} is:issue is:open"),
        stdout=_search_response([record]),
    )
    result = await _call_list(registry, tmp_path, monkeypatch)
    assert result.items[0].state == "closed"


# ---------------------------------------------------------------------------
# Limit
# ---------------------------------------------------------------------------


async def test_list_limit_caps_results(
    registry: FakeSubprocessRegistry, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    records = [{**_SAMPLE_RECORD, "number": i} for i in range(1, 6)]
    register_repo(registry)
    registry.expect(
        _search_argv(f"repo:{_REPO} is:issue is:open"),
        stdout=_search_response(records, total=100),
    )
    result = await _call_list(
        registry,
        tmp_path,
        monkeypatch,
        filter=ItemListFilter(limit=3),
    )
    assert len(result.items) == 3


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _empty_response_with_total(total: int) -> bytes:
    return json.dumps({"total_count": total, "incomplete_results": False, "items": []}).encode()


def _find_search_call(registry: FakeSubprocessRegistry) -> tuple[str, ...]:
    return next(c for c in registry.calls if "api" in c and "search/issues" in c)


def _query_from_call(call: tuple[str, ...]) -> str:
    """Extract the q= value from an argv tuple."""
    args = list(call)
    for i, arg in enumerate(args):
        if arg == "-f" and i + 1 < len(args) and args[i + 1].startswith("q="):
            return args[i + 1][2:]
    return ""
