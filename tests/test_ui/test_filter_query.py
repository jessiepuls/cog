"""Tests for the typed-query filter: parse_query, apply_parsed, FilterSuggester (#200)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from cog.ui.widgets.filter_query import (
    FilterSuggester,
    ParsedQuery,
    apply_parsed,
    parse_query,
)
from tests.fakes import make_item

_BASE_DT = datetime(2026, 1, 1, tzinfo=UTC)


def _item(
    item_id: str = "1",
    title: str = "title",
    labels: tuple[str, ...] = (),
    state: str = "open",
    assignees: tuple[str, ...] = (),
) -> object:
    return make_item(
        item_id=item_id,
        title=title,
        labels=labels,
        state=state,
        assignees=assignees,
    )


# ---------------------------------------------------------------------------
# parse_query — state handling
# ---------------------------------------------------------------------------


def test_parse_state_open() -> None:
    q = parse_query("state:open")
    assert q.state_set == frozenset({"open"})


def test_parse_state_closed() -> None:
    q = parse_query("state:closed")
    assert q.state_set == frozenset({"closed"})


def test_parse_state_all_expands() -> None:
    q = parse_query("state:all")
    assert q.state_set == frozenset({"open", "closed"})


def test_parse_state_open_comma_closed() -> None:
    q = parse_query("state:open,closed")
    assert q.state_set == frozenset({"open", "closed"})


def test_parse_state_unrecognized_contributes_nothing() -> None:
    q = parse_query("state:foo")
    assert q.state_set is None


def test_parse_state_partial_contributes_nothing() -> None:
    q = parse_query("state:o")
    assert q.state_set is None


def test_parse_state_alone_contributes_nothing() -> None:
    q = parse_query("state:")
    assert q.state_set is None


# ---------------------------------------------------------------------------
# parse_query — label handling
# ---------------------------------------------------------------------------


def test_parse_single_label() -> None:
    q = parse_query("label:bug")
    assert q.label_groups == (frozenset({"bug"}),)


def test_parse_comma_label_is_or_group() -> None:
    q = parse_query("label:bug,docs")
    assert q.label_groups == (frozenset({"bug", "docs"}),)


def test_parse_repeated_label_is_and() -> None:
    q = parse_query("label:bug label:docs")
    assert len(q.label_groups) == 2
    assert frozenset({"bug"}) in q.label_groups
    assert frozenset({"docs"}) in q.label_groups


def test_parse_quoted_label() -> None:
    q = parse_query('label:"good first issue"')
    assert q.label_groups == (frozenset({"good first issue"}),)


def test_parse_label_alone_contributes_nothing() -> None:
    q = parse_query("label:")
    assert q.label_groups == ()


# ---------------------------------------------------------------------------
# parse_query — assignee handling
# ---------------------------------------------------------------------------


def test_parse_assignee_login() -> None:
    q = parse_query("assignee:alice")
    assert q.assignee_groups == (frozenset({"alice"}),)


def test_parse_assignee_me() -> None:
    q = parse_query("assignee:me")
    assert q.assignee_groups == (frozenset({"me"}),)


def test_parse_assignee_unassigned() -> None:
    q = parse_query("assignee:unassigned")
    assert q.assignee_groups == (frozenset({"unassigned"}),)


def test_parse_assignee_comma_or() -> None:
    q = parse_query("assignee:alice,bob")
    assert q.assignee_groups == (frozenset({"alice", "bob"}),)


# ---------------------------------------------------------------------------
# parse_query — barewords
# ---------------------------------------------------------------------------


def test_parse_bareword() -> None:
    q = parse_query("bug")
    assert q.barewords == ("bug",)


def test_parse_multiple_barewords_are_and() -> None:
    q = parse_query("bug parser")
    assert set(q.barewords) == {"bug", "parser"}


def test_parse_quoted_bareword() -> None:
    q = parse_query('"bug parser"')
    assert q.quoted_barewords == ("bug parser",)
    assert q.barewords == ()


def test_parse_numeric_bareword() -> None:
    q = parse_query("42")
    assert q.barewords == ("42",)


def test_parse_hash_numeric_bareword() -> None:
    q = parse_query("#42")
    assert q.barewords == ("#42",)


# ---------------------------------------------------------------------------
# parse_query — tolerance
# ---------------------------------------------------------------------------


def test_parse_empty_is_no_op() -> None:
    q = parse_query("")
    assert q == ParsedQuery()


def test_parse_whitespace_is_no_op() -> None:
    q = parse_query("   ")
    assert q == ParsedQuery()


def test_parse_unbalanced_quote_drops_partial() -> None:
    # Unbalanced quote is a partial token — contributes no constraint
    q = parse_query('label:"good fir')
    assert q.label_groups == ()


def test_parse_unknown_key_becomes_bareword() -> None:
    # "foo:bar" — unknown key, falls through to bareword
    q = parse_query("foo:bar")
    assert "foo:bar" in q.barewords


# ---------------------------------------------------------------------------
# apply_parsed — state filter
# ---------------------------------------------------------------------------


def test_apply_state_open_excludes_closed() -> None:
    items = [_item("1", state="open"), _item("2", state="closed")]
    q = parse_query("state:open")
    result = apply_parsed(items, q, current_user_login=None)
    assert [i.item_id for i in result] == ["1"]


def test_apply_state_closed_excludes_open() -> None:
    items = [_item("1", state="open"), _item("2", state="closed")]
    q = parse_query("state:closed")
    result = apply_parsed(items, q, current_user_login=None)
    assert [i.item_id for i in result] == ["2"]


def test_apply_state_all_includes_both() -> None:
    items = [_item("1", state="open"), _item("2", state="closed")]
    q = parse_query("state:all")
    result = apply_parsed(items, q, current_user_login=None)
    assert {i.item_id for i in result} == {"1", "2"}


def test_apply_no_state_includes_everything() -> None:
    items = [_item("1", state="open"), _item("2", state="closed")]
    q = parse_query("")
    result = apply_parsed(items, q, current_user_login=None)
    assert {i.item_id for i in result} == {"1", "2"}


# ---------------------------------------------------------------------------
# apply_parsed — label filter
# ---------------------------------------------------------------------------


def test_apply_label_or_within_token() -> None:
    items = [
        _item("1", labels=("bug",)),
        _item("2", labels=("docs",)),
        _item("3", labels=("enhancement",)),
    ]
    q = parse_query("label:bug,docs")
    result = apply_parsed(items, q, current_user_login=None)
    assert {i.item_id for i in result} == {"1", "2"}


def test_apply_repeated_label_is_and() -> None:
    items = [
        _item("1", labels=("bug", "docs")),
        _item("2", labels=("bug",)),
        _item("3", labels=("docs",)),
    ]
    q = parse_query("label:bug label:docs")
    result = apply_parsed(items, q, current_user_login=None)
    assert [i.item_id for i in result] == ["1"]


def test_apply_label_case_insensitive() -> None:
    items = [_item("1", labels=("Bug",))]
    q = parse_query("label:bug")
    result = apply_parsed(items, q, current_user_login=None)
    assert len(result) == 1


# ---------------------------------------------------------------------------
# apply_parsed — assignee filter
# ---------------------------------------------------------------------------


def test_apply_assignee_login() -> None:
    items = [_item("1", assignees=("alice",)), _item("2", assignees=("bob",))]
    q = parse_query("assignee:alice")
    result = apply_parsed(items, q, current_user_login=None)
    assert [i.item_id for i in result] == ["1"]


def test_apply_assignee_unassigned() -> None:
    items = [_item("1", assignees=()), _item("2", assignees=("alice",))]
    q = parse_query("assignee:unassigned")
    result = apply_parsed(items, q, current_user_login=None)
    assert [i.item_id for i in result] == ["1"]


def test_apply_assignee_me_substituted() -> None:
    items = [_item("1", assignees=("alice",)), _item("2", assignees=("bob",))]
    q = parse_query("assignee:me")
    result = apply_parsed(items, q, current_user_login="alice")
    assert [i.item_id for i in result] == ["1"]


def test_apply_assignee_me_unresolved_matches_nothing() -> None:
    items = [_item("1", assignees=("alice",))]
    q = parse_query("assignee:me")
    result = apply_parsed(items, q, current_user_login=None)
    # 'me' unresolved: group is {"me"}, no item has assignee "me"
    assert result == []


# ---------------------------------------------------------------------------
# apply_parsed — barewords
# ---------------------------------------------------------------------------


def test_apply_bareword_title_match() -> None:
    items = [_item("1", title="login bug"), _item("2", title="parser error")]
    q = parse_query("bug")
    result = apply_parsed(items, q, current_user_login=None)
    assert [i.item_id for i in result] == ["1"]


def test_apply_multiple_barewords_and() -> None:
    items = [
        _item("1", title="login bug parser"),
        _item("2", title="login bug"),
        _item("3", title="parser only"),
    ]
    q = parse_query("bug parser")
    result = apply_parsed(items, q, current_user_login=None)
    assert [i.item_id for i in result] == ["1"]


def test_apply_numeric_bareword_matches_id() -> None:
    items = [_item("42", title="unrelated"), _item("1", title="issue 42")]
    q = parse_query("42")
    result = apply_parsed(items, q, current_user_login=None)
    assert {i.item_id for i in result} == {"42", "1"}


def test_apply_hash_numeric_bareword_matches_id() -> None:
    items = [_item("42", title="unrelated")]
    q = parse_query("#42")
    result = apply_parsed(items, q, current_user_login=None)
    assert [i.item_id for i in result] == ["42"]


def test_apply_quoted_bareword_no_id_shortcut() -> None:
    items = [_item("42", title="unrelated"), _item("1", title="issue about 42")]
    q = parse_query('"42"')
    result = apply_parsed(items, q, current_user_login=None)
    # Quoted bareword: no item_id shortcut, only title match
    assert [i.item_id for i in result] == ["1"]


def test_apply_bare_case_insensitive() -> None:
    items = [_item("1", title="Login Bug")]
    q = parse_query("bug")
    result = apply_parsed(items, q, current_user_login=None)
    assert len(result) == 1


# ---------------------------------------------------------------------------
# FilterSuggester — key completions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_suggester_l_suggests_label() -> None:
    s = FilterSuggester(
        get_labels=lambda: [],
        get_assignees=lambda: [],
        get_current_user_login=lambda: None,
    )
    assert await s.get_suggestion("l") == "label:"


@pytest.mark.asyncio
async def test_suggester_a_suggests_assignee() -> None:
    s = FilterSuggester(
        get_labels=lambda: [],
        get_assignees=lambda: [],
        get_current_user_login=lambda: None,
    )
    assert await s.get_suggestion("a") == "assignee:"


@pytest.mark.asyncio
async def test_suggester_s_suggests_state() -> None:
    s = FilterSuggester(
        get_labels=lambda: [],
        get_assignees=lambda: [],
        get_current_user_login=lambda: None,
    )
    assert await s.get_suggestion("s") == "state:"


@pytest.mark.asyncio
async def test_suggester_b_no_key_suggestion() -> None:
    s = FilterSuggester(
        get_labels=lambda: [],
        get_assignees=lambda: [],
        get_current_user_login=lambda: None,
    )
    assert await s.get_suggestion("b") is None


@pytest.mark.asyncio
async def test_suggester_trailing_space_no_suggestion() -> None:
    s = FilterSuggester(
        get_labels=lambda: [],
        get_assignees=lambda: [],
        get_current_user_login=lambda: None,
    )
    assert await s.get_suggestion("state:open ") is None


# ---------------------------------------------------------------------------
# FilterSuggester — value completions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_suggester_state_value() -> None:
    s = FilterSuggester(
        get_labels=lambda: [],
        get_assignees=lambda: [],
        get_current_user_login=lambda: None,
    )
    assert await s.get_suggestion("state:o") == "state:open"
    assert await s.get_suggestion("state:c") == "state:closed"
    assert await s.get_suggestion("state:a") == "state:all"


@pytest.mark.asyncio
async def test_suggester_state_already_complete_no_suggestion() -> None:
    s = FilterSuggester(
        get_labels=lambda: [],
        get_assignees=lambda: [],
        get_current_user_login=lambda: None,
    )
    assert await s.get_suggestion("state:open") is None


@pytest.mark.asyncio
async def test_suggester_label_value() -> None:
    s = FilterSuggester(
        get_labels=lambda: ["bug", "docs", "enhancement"],
        get_assignees=lambda: [],
        get_current_user_login=lambda: None,
    )
    assert await s.get_suggestion("label:b") == "label:bug"
    assert await s.get_suggestion("label:d") == "label:docs"


@pytest.mark.asyncio
async def test_suggester_label_quoted_when_space() -> None:
    s = FilterSuggester(
        get_labels=lambda: ["good first issue"],
        get_assignees=lambda: [],
        get_current_user_login=lambda: None,
    )
    result = await s.get_suggestion("label:g")
    assert result == 'label:"good first issue"'


@pytest.mark.asyncio
async def test_suggester_label_comma_continuation() -> None:
    s = FilterSuggester(
        get_labels=lambda: ["bug", "docs"],
        get_assignees=lambda: [],
        get_current_user_login=lambda: None,
    )
    # After "label:bug,", completing "d" should give "label:bug,docs"
    result = await s.get_suggestion("label:bug,d")
    assert result == "label:bug,docs"


@pytest.mark.asyncio
async def test_suggester_label_comma_excludes_existing() -> None:
    s = FilterSuggester(
        get_labels=lambda: ["bug", "docs"],
        get_assignees=lambda: [],
        get_current_user_login=lambda: None,
    )
    # "bug" already listed, partial "" should give "docs" next
    result = await s.get_suggestion("label:bug,")
    assert result == "label:bug,docs"


@pytest.mark.asyncio
async def test_suggester_assignee_me_when_login_known() -> None:
    s = FilterSuggester(
        get_labels=lambda: [],
        get_assignees=lambda: [],
        get_current_user_login=lambda: "alice",
    )
    result = await s.get_suggestion("assignee:m")
    assert result == "assignee:me"


@pytest.mark.asyncio
async def test_suggester_assignee_unassigned() -> None:
    s = FilterSuggester(
        get_labels=lambda: [],
        get_assignees=lambda: [],
        get_current_user_login=lambda: None,
    )
    result = await s.get_suggestion("assignee:u")
    assert result == "assignee:unassigned"


@pytest.mark.asyncio
async def test_suggester_repeated_key_exclusion() -> None:
    s = FilterSuggester(
        get_labels=lambda: ["bug", "docs"],
        get_assignees=lambda: [],
        get_current_user_login=lambda: None,
    )
    # "label:bug " already in prefix; now completing "label:d"
    result = await s.get_suggestion("label:bug label:d")
    assert result == "label:bug label:docs"


@pytest.mark.asyncio
async def test_suggester_second_token_after_space() -> None:
    s = FilterSuggester(
        get_labels=lambda: [],
        get_assignees=lambda: [],
        get_current_user_login=lambda: None,
    )
    # After "state:open ", completing "l"
    result = await s.get_suggestion("state:open l")
    assert result == "state:open label:"
