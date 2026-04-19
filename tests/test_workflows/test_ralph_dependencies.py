"""Tests for RalphWorkflow dependency safety net (#15)."""

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock

from cog.core.errors import TrackerError
from cog.core.item import Comment
from cog.core.tracker import IssueTracker
from cog.workflows.ralph import _BLOCKER_RE, RalphWorkflow
from tests.fakes import InMemoryStateCache, make_item, make_item_with_blocker_refs


def _make_workflow() -> RalphWorkflow:
    return RalphWorkflow(runner=AsyncMock(), tracker=AsyncMock(spec=IssueTracker))


def _make_ctx(cache: InMemoryStateCache | None = None, *, telemetry=None):
    from cog.core.context import ExecutionContext

    ctx = ExecutionContext(
        project_dir=Path("/tmp"),
        tmp_dir=Path("/tmp"),
        state_cache=cache or InMemoryStateCache(),
        headless=True,
    )
    ctx.telemetry = telemetry
    return ctx


# --- Regex tests ---


def test_blocker_regex_matches_blocked_by() -> None:
    assert _BLOCKER_RE.search("blocked by #42") is not None


def test_blocker_regex_matches_depends_on() -> None:
    assert _BLOCKER_RE.search("depends on #7") is not None


def test_blocker_regex_case_insensitive() -> None:
    assert _BLOCKER_RE.search("Blocked By #1") is not None
    assert _BLOCKER_RE.search("DEPENDS ON #2") is not None


def test_blocker_regex_extracts_number() -> None:
    m = _BLOCKER_RE.search("blocked by #123")
    assert m is not None
    assert m.group(1) == "123"


def test_blocker_regex_multiple_refs_in_one_source() -> None:
    text = "blocked by #1 and depends on #2"
    matches = {int(m.group(1)) for m in _BLOCKER_RE.finditer(text)}
    assert matches == {1, 2}


def test_blocker_regex_matches_across_line_boundaries() -> None:
    text = "blocked by\n#99"
    assert _BLOCKER_RE.search(text) is not None


def test_blocker_regex_ignores_close_keywords() -> None:
    assert _BLOCKER_RE.search("Closes #42") is None
    assert _BLOCKER_RE.search("Fixes #42") is None
    assert _BLOCKER_RE.search("Resolves #42") is None


def test_blocker_regex_ignores_bare_hash_refs() -> None:
    assert _BLOCKER_RE.search("see #42") is None


# --- _find_open_blockers tests ---


async def test_find_open_blockers_empty_when_no_refs() -> None:
    wf = _make_workflow()
    item = make_item(body="no references here")
    result = await wf._find_open_blockers(item)
    assert result == []


async def test_find_open_blockers_dedupes_across_body_and_comments() -> None:
    comment = Comment(
        author="alice", body="blocked by #42", created_at=datetime(2024, 1, 1, tzinfo=UTC)
    )
    item = make_item(body="blocked by #42", comments=(comment,))
    blocker = make_item(item_id="42", state="open")
    wf = _make_workflow()
    wf._tracker.get = AsyncMock(return_value=blocker)

    result = await wf._find_open_blockers(item)

    assert result == [42]
    wf._tracker.get.assert_awaited_once_with("42")


async def test_find_open_blockers_returns_only_open_ones() -> None:
    item = make_item(body="blocked by #1 and depends on #2")
    blocker_open = make_item(item_id="1", state="open")
    blocker_closed = make_item(item_id="2", state="closed")
    wf = _make_workflow()
    wf._tracker.get = AsyncMock(
        side_effect=lambda iid: blocker_open if iid == "1" else blocker_closed
    )

    result = await wf._find_open_blockers(item)

    assert result == [1]


async def test_find_open_blockers_tolerates_ghost_refs() -> None:
    item = make_item(body="blocked by #99 and depends on #100")
    real_blocker = make_item(item_id="100", state="open")
    wf = _make_workflow()

    def side_effect(iid: str):
        if iid == "99":
            raise TrackerError("not found")
        return real_blocker

    wf._tracker.get = AsyncMock(side_effect=side_effect)

    result = await wf._find_open_blockers(item)

    assert result == [100]


async def test_find_open_blockers_sorts_numerically() -> None:
    item = make_item(body="depends on #10 and blocked by #2 and blocked by #5")
    wf = _make_workflow()
    wf._tracker.get = AsyncMock(side_effect=lambda iid: make_item(item_id=iid, state="open"))

    result = await wf._find_open_blockers(item)

    assert result == [2, 5, 10]


# --- select_item integration tests ---


async def test_select_item_defers_when_blocker_open() -> None:
    item_blocked = make_item_with_blocker_refs([99], item_id="5")
    item_free = make_item(item_id="6")
    cache = InMemoryStateCache()
    wf = _make_workflow()
    wf._tracker.list_by_label = AsyncMock(return_value=[item_blocked, item_free])

    blocker = make_item(item_id="99", state="open")

    def get_side_effect(iid: str):
        if iid == "5":
            return make_item_with_blocker_refs([99], item_id="5")
        if iid == "6":
            return make_item(item_id="6")
        if iid == "99":
            return blocker
        raise AssertionError(f"unexpected get({iid!r})")

    wf._tracker.get = AsyncMock(side_effect=get_side_effect)
    ctx = _make_ctx(cache)

    result = await wf.select_item(ctx)

    assert result is not None
    assert result.item_id == "6"
    assert cache.is_deferred(item_blocked)


async def test_select_item_returns_when_all_blockers_closed() -> None:
    item = make_item_with_blocker_refs([10], item_id="5")
    blocker = make_item(item_id="10", state="closed")
    cache = InMemoryStateCache()
    wf = _make_workflow()
    wf._tracker.list_by_label = AsyncMock(return_value=[item])

    def get_side_effect(iid: str):
        if iid == "5":
            return make_item_with_blocker_refs([10], item_id="5")
        return blocker

    wf._tracker.get = AsyncMock(side_effect=get_side_effect)
    ctx = _make_ctx(cache)

    result = await wf.select_item(ctx)

    assert result is not None
    assert result.item_id == "5"


async def test_select_item_returns_when_no_blocker_refs() -> None:
    item = make_item(item_id="3", body="No dependencies here.")
    wf = _make_workflow()
    wf._tracker.list_by_label = AsyncMock(return_value=[item])
    wf._tracker.get = AsyncMock(return_value=make_item(item_id="3"))
    ctx = _make_ctx()

    result = await wf.select_item(ctx)

    assert result is not None
    assert result.item_id == "3"


async def test_select_item_returns_none_when_all_eligible_blocked() -> None:
    item = make_item_with_blocker_refs([1], item_id="5")
    blocker = make_item(item_id="1", state="open")
    wf = _make_workflow()
    wf._tracker.list_by_label = AsyncMock(return_value=[item])

    def get_side_effect(iid: str):
        if iid == "5":
            return make_item_with_blocker_refs([1], item_id="5")
        return blocker

    wf._tracker.get = AsyncMock(side_effect=get_side_effect)
    ctx = _make_ctx()

    result = await wf.select_item(ctx)

    assert result is None


async def test_select_item_deferred_candidate_added_to_processed_this_loop() -> None:
    item = make_item_with_blocker_refs([1], item_id="5")
    blocker = make_item(item_id="1", state="open")
    wf = _make_workflow()
    wf._tracker.list_by_label = AsyncMock(return_value=[item])

    def get_side_effect(iid: str):
        if iid == "5":
            return make_item_with_blocker_refs([1], item_id="5")
        return blocker

    wf._tracker.get = AsyncMock(side_effect=get_side_effect)
    ctx = _make_ctx()

    await wf.select_item(ctx)

    assert ("github/org/repo", "5") in wf._processed_this_loop


async def test_select_item_does_not_filter_is_deferred_up_front() -> None:
    """Regression guard: a previously-deferred item whose blocker has closed is revived."""
    item = make_item_with_blocker_refs([10], item_id="5")
    cache = InMemoryStateCache()
    cache.mark_deferred(item, "blocker", ["10"])
    blocker_now_closed = make_item(item_id="10", state="closed")
    wf = _make_workflow()
    wf._tracker.list_by_label = AsyncMock(return_value=[item])

    def get_side_effect(iid: str):
        if iid == "5":
            return make_item_with_blocker_refs([10], item_id="5")
        return blocker_now_closed

    wf._tracker.get = AsyncMock(side_effect=get_side_effect)
    ctx = _make_ctx(cache)

    result = await wf.select_item(ctx)

    assert result is not None
    assert result.item_id == "5"


async def test_select_item_clears_deferral_on_successful_selection() -> None:
    item = make_item_with_blocker_refs([10], item_id="5")
    cache = InMemoryStateCache()
    cache.mark_deferred(item, "blocker", ["10"])
    blocker_closed = make_item(item_id="10", state="closed")
    wf = _make_workflow()
    wf._tracker.list_by_label = AsyncMock(return_value=[item])

    def get_side_effect(iid: str):
        if iid == "5":
            return make_item_with_blocker_refs([10], item_id="5")
        return blocker_closed

    wf._tracker.get = AsyncMock(side_effect=get_side_effect)
    ctx = _make_ctx(cache)

    await wf.select_item(ctx)

    assert not cache.is_deferred(make_item(item_id="5"))


async def test_select_item_saves_state_cache_after_deferral() -> None:
    item = make_item_with_blocker_refs([1], item_id="5")
    blocker = make_item(item_id="1", state="open")
    cache = InMemoryStateCache()
    save_calls = []
    cache.save = lambda: save_calls.append(True)  # type: ignore[method-assign]
    wf = _make_workflow()
    wf._tracker.list_by_label = AsyncMock(return_value=[item])

    def get_side_effect(iid: str):
        if iid == "5":
            return make_item_with_blocker_refs([1], item_id="5")
        return blocker

    wf._tracker.get = AsyncMock(side_effect=get_side_effect)
    ctx = _make_ctx(cache)

    await wf.select_item(ctx)

    assert len(save_calls) >= 1


# --- Telemetry tests ---


async def test_deferred_telemetry_outcome_is_deferred_by_blocker() -> None:
    wf = _make_workflow()
    item = make_item(item_id="5")
    telemetry = AsyncMock()
    ctx = _make_ctx(telemetry=telemetry)

    await wf._write_deferred_telemetry(ctx, item, [3])

    telemetry.write.assert_awaited_once()
    record = telemetry.write.call_args[0][0]
    assert record.outcome == "deferred-by-blocker"


async def test_deferred_telemetry_error_encodes_blocker_list() -> None:
    wf = _make_workflow()
    item = make_item(item_id="5")
    telemetry = AsyncMock()
    ctx = _make_ctx(telemetry=telemetry)

    await wf._write_deferred_telemetry(ctx, item, [1, 2])

    record = telemetry.write.call_args[0][0]
    assert record.error == "blocked by: #1, #2"


async def test_deferred_telemetry_skipped_when_ctx_telemetry_is_none() -> None:
    wf = _make_workflow()
    item = make_item(item_id="5")
    ctx = _make_ctx(telemetry=None)

    # Should not raise
    await wf._write_deferred_telemetry(ctx, item, [1])


# --- Self-healing integration test ---


async def test_deferred_item_revives_when_blocker_closes() -> None:
    """Two separate workflow runs share a state cache. Item deferred in run 1,
    revived in run 2 when blocker closes."""
    cache = InMemoryStateCache()
    item = make_item_with_blocker_refs([10], item_id="5")

    blocker_open = make_item(item_id="10", state="open")
    blocker_closed = make_item(item_id="10", state="closed")

    def get_open(iid: str):
        if iid == "5":
            return make_item_with_blocker_refs([10], item_id="5")
        return blocker_open

    def get_closed(iid: str):
        if iid == "5":
            return make_item_with_blocker_refs([10], item_id="5")
        return blocker_closed

    # Run 1: blocker open → item deferred
    wf1 = _make_workflow()
    wf1._tracker.list_by_label = AsyncMock(return_value=[item])
    wf1._tracker.get = AsyncMock(side_effect=get_open)
    ctx1 = _make_ctx(cache)
    result1 = await wf1.select_item(ctx1)
    assert result1 is None
    assert cache.is_deferred(item)

    # Run 2: blocker now closed → item revived
    wf2 = _make_workflow()
    wf2._tracker.list_by_label = AsyncMock(return_value=[item])
    wf2._tracker.get = AsyncMock(side_effect=get_closed)
    ctx2 = _make_ctx(cache)
    result2 = await wf2.select_item(ctx2)
    assert result2 is not None
    assert result2.item_id == "5"
    assert not cache.is_deferred(item)
