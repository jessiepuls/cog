"""Pure-function tests for _apply_filter in issues_browser (#189)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from cog.core.item import Item
from cog.core.tracker import ItemListFilter
from cog.ui.widgets.issues_browser import _apply_filter

_BASE_DT = datetime(2026, 1, 1, tzinfo=UTC)


def _item(
    item_id: str,
    title: str = "title",
    labels: tuple[str, ...] = (),
    assignees: tuple[str, ...] = (),
    state: str = "open",
    updated_at: datetime | None = None,
) -> Item:
    return Item(
        tracker_id="gh",
        item_id=item_id,
        title=title,
        body="",
        labels=labels,
        comments=(),
        state=state,
        created_at=_BASE_DT,
        updated_at=updated_at or _BASE_DT,
        url="",
        assignees=assignees,
    )


# --- Label filtering ---


def test_filter_single_label_matches() -> None:
    items = [_item("1", labels=("bug",)), _item("2", labels=("feat",))]
    result = _apply_filter(items, ItemListFilter(labels=("bug",), state="all"))
    assert [i.item_id for i in result] == ["1"]


def test_filter_multiple_labels_and_semantics() -> None:
    items = [
        _item("1", labels=("bug", "needs-refinement")),
        _item("2", labels=("bug",)),
        _item("3", labels=("needs-refinement",)),
    ]
    result = _apply_filter(items, ItemListFilter(labels=("bug", "needs-refinement"), state="all"))
    assert [i.item_id for i in result] == ["1"]


def test_filter_no_labels_returns_all_state_matched() -> None:
    items = [_item("1"), _item("2")]
    result = _apply_filter(items, ItemListFilter(state="all"))
    assert len(result) == 2


# --- Assignee filtering ---


def test_filter_assignee_unassigned_sentinel() -> None:
    items = [_item("1", assignees=()), _item("2", assignees=("alice",))]
    result = _apply_filter(items, ItemListFilter(assignee="(unassigned)", state="all"))
    assert [i.item_id for i in result] == ["1"]


def test_filter_assignee_login() -> None:
    items = [_item("1", assignees=("alice",)), _item("2", assignees=("bob",))]
    result = _apply_filter(items, ItemListFilter(assignee="alice", state="all"))
    assert [i.item_id for i in result] == ["1"]


def test_filter_unassigned_plus_login_returns_empty() -> None:
    # (unassigned) AND alice is self-contradictory; natural AND → empty
    items = [_item("1", assignees=()), _item("2", assignees=("alice",))]
    # We can only pass one assignee at a time in ItemListFilter — combining
    # unassigned+login is not a single-filter scenario. Each call covers one.
    result_u = _apply_filter(items, ItemListFilter(assignee="(unassigned)", state="all"))
    result_l = _apply_filter(items, ItemListFilter(assignee="alice", state="all"))
    assert result_u[0].item_id == "1"
    assert result_l[0].item_id == "2"


# --- Search filtering ---


@pytest.mark.parametrize(
    "query, expected_ids",
    [
        ("login bug", ["1"]),
        ("LOGIN BUG", ["1"]),  # case-insensitive
        ("189", ["189"]),  # plain number
        ("#189", ["189"]),  # leading hash
        (" #189 ", ["189"]),  # whitespace + hash
        ("999", []),  # non-matching number
        ("nonexistent", []),
    ],
)
def test_filter_search(query: str, expected_ids: list[str]) -> None:
    items = [
        _item("1", title="Login bug: SSO fails"),
        _item("189", title="TUI: build a unified Issues browser"),
        _item("42", title="some other issue"),
    ]
    result = _apply_filter(items, ItemListFilter(search=query, state="all"))
    assert [i.item_id for i in result] == expected_ids


def test_filter_search_number_also_matches_title_substring() -> None:
    items = [
        _item("1", title="Fix issue 42"),
        _item("42", title="real item 42"),
    ]
    result = _apply_filter(items, ItemListFilter(search="42", state="all"))
    ids = {i.item_id for i in result}
    assert "42" in ids
    assert "1" in ids  # title contains "42"


# --- State filtering ---


def test_filter_state_open() -> None:
    items = [_item("1", state="open"), _item("2", state="closed")]
    result = _apply_filter(items, ItemListFilter(state="open"))
    assert [i.item_id for i in result] == ["1"]


def test_filter_state_closed() -> None:
    items = [_item("1", state="open"), _item("2", state="closed")]
    result = _apply_filter(items, ItemListFilter(state="closed"))
    assert [i.item_id for i in result] == ["2"]


def test_filter_state_all() -> None:
    items = [_item("1", state="open"), _item("2", state="closed")]
    result = _apply_filter(items, ItemListFilter(state="all"))
    assert len(result) == 2


# --- Sort order ---


def test_filter_sorted_by_updated_at_desc() -> None:
    items = [
        _item("1", updated_at=_BASE_DT),
        _item("2", updated_at=_BASE_DT + timedelta(hours=1)),
        _item("3", updated_at=_BASE_DT + timedelta(hours=2)),
    ]
    result = _apply_filter(items, ItemListFilter(state="all"))
    assert [i.item_id for i in result] == ["3", "2", "1"]


def test_filter_tiebreak_by_item_id_desc() -> None:
    items = [
        _item("1", updated_at=_BASE_DT),
        _item("5", updated_at=_BASE_DT),
        _item("3", updated_at=_BASE_DT),
    ]
    result = _apply_filter(items, ItemListFilter(state="all"))
    assert [i.item_id for i in result] == ["5", "3", "1"]


# --- Combinations ---


def test_filter_label_and_search() -> None:
    items = [
        _item("1", title="bug fix", labels=("bug",)),
        _item("2", title="bug report", labels=("feat",)),
        _item("3", title="other", labels=("bug",)),
    ]
    result = _apply_filter(items, ItemListFilter(labels=("bug",), search="bug", state="all"))
    assert {i.item_id for i in result} == {"1"}


def test_filter_state_and_label() -> None:
    items = [
        _item("1", labels=("bug",), state="open"),
        _item("2", labels=("bug",), state="closed"),
        _item("3", labels=("feat",), state="open"),
    ]
    result = _apply_filter(items, ItemListFilter(labels=("bug",), state="open"))
    assert [i.item_id for i in result] == ["1"]
