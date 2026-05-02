"""Tests for DynamicSlotRegistry and DynamicSlot (#192)."""

from __future__ import annotations

import pytest

from cog.ui.dynamic_slots import DynamicSlot, DynamicSlotRegistry, max_concurrent_implements

# ---------------------------------------------------------------------------
# DynamicSlot
# ---------------------------------------------------------------------------


def _slot(
    run_id: str = "abc",
    workflow: str = "implement",
    item_id: str = "1",
    state: str = "running",
    stage: str = "",
) -> DynamicSlot:
    return DynamicSlot(run_id=run_id, workflow=workflow, item_id=item_id, state=state, stage=stage)  # type: ignore[arg-type]


def test_slot_key() -> None:
    slot = _slot(workflow="implement", item_id="42")
    assert slot.slot_key == ("implement", "42")


def test_slot_sidebar_label_running() -> None:
    slot = _slot(run_id="x", workflow="implement", item_id="42", stage="build")
    label = slot.sidebar_label("ctrl+6")
    assert "^6" in label
    assert "I" in label
    assert "#42" in label
    assert "build" in label
    assert "●" in label  # green running dot


def test_slot_sidebar_label_awaiting_dismiss() -> None:
    slot = _slot(workflow="refine", item_id="7", state="awaiting_dismiss", stage="review")
    label = slot.sidebar_label("ctrl+7")
    assert "R" in label
    assert "#7" in label
    assert "◐" in label  # yellow


def test_slot_sidebar_label_errored() -> None:
    slot = _slot(state="awaiting_dismiss", stage="build")
    slot.errored = True
    label = slot.sidebar_label("ctrl+6")
    assert "✕" in label


def test_slot_sidebar_label_stage_truncated() -> None:
    slot = _slot(stage="averylongstagenamehereXXX")
    label = slot.sidebar_label("ctrl+6")
    # Stage is truncated to 10 chars
    assert "averylongs" in label
    assert "XXX" not in label


# ---------------------------------------------------------------------------
# DynamicSlotRegistry
# ---------------------------------------------------------------------------


def test_registry_add_and_len() -> None:
    reg = DynamicSlotRegistry()
    reg.add(_slot("a"))
    reg.add(_slot("b", item_id="2"))
    assert len(reg) == 2


def test_registry_on_change_called_on_add() -> None:
    calls: list[str] = []
    reg = DynamicSlotRegistry(on_change=lambda: calls.append("change"))
    reg.add(_slot("a"))
    assert calls == ["change"]


def test_registry_on_change_called_on_remove() -> None:
    calls: list[str] = []
    reg = DynamicSlotRegistry(on_change=lambda: calls.append("change"))
    reg.add(_slot("a"))
    calls.clear()
    reg.remove("a")
    assert calls == ["change"]


def test_registry_remove_unknown_run_id_is_noop() -> None:
    reg = DynamicSlotRegistry()
    reg.add(_slot("a"))
    reg.remove("nope")
    assert len(reg) == 1


def test_registry_get_by_workflow_and_item_id() -> None:
    reg = DynamicSlotRegistry()
    s = _slot("a", workflow="refine", item_id="10")
    reg.add(s)
    found = reg.get("refine", "10")
    assert found is s
    assert reg.get("implement", "10") is None
    assert reg.get("refine", "99") is None


def test_registry_get_by_run_id() -> None:
    reg = DynamicSlotRegistry()
    s = _slot("abc123")
    reg.add(s)
    assert reg.get_by_run_id("abc123") is s
    assert reg.get_by_run_id("nope") is None


def test_registry_active_count_excludes_closed() -> None:
    reg = DynamicSlotRegistry()
    reg.add(_slot("a", state="running"))
    reg.add(_slot("b", item_id="2", state="awaiting_dismiss"))
    reg.add(_slot("c", item_id="3", state="closed"))
    assert reg.active_count() == 2


def test_registry_active_count_by_workflow() -> None:
    reg = DynamicSlotRegistry()
    reg.add(_slot("a", workflow="implement", item_id="1"))
    reg.add(_slot("b", workflow="implement", item_id="2"))
    reg.add(_slot("c", workflow="refine", item_id="3"))
    assert reg.active_count("implement") == 2
    assert reg.active_count("refine") == 1


def test_registry_update_state() -> None:
    calls: list[str] = []
    reg = DynamicSlotRegistry(on_change=lambda: calls.append("c"))
    reg.add(_slot("a"))
    calls.clear()
    reg.update_state("a", "awaiting_dismiss")
    s = reg.get_by_run_id("a")
    assert s is not None
    assert s.state == "awaiting_dismiss"
    assert calls == ["c"]


def test_registry_update_state_errored_flag() -> None:
    reg = DynamicSlotRegistry()
    reg.add(_slot("a"))
    reg.update_state("a", "awaiting_dismiss", errored=True)
    s = reg.get_by_run_id("a")
    assert s is not None
    assert s.errored is True


def test_registry_update_stage() -> None:
    calls: list[str] = []
    reg = DynamicSlotRegistry(on_change=lambda: calls.append("c"))
    reg.add(_slot("a"))
    calls.clear()
    reg.update_stage("a", "review")
    s = reg.get_by_run_id("a")
    assert s is not None
    assert s.stage == "review"
    assert calls == ["c"]


def test_registry_active_slots_excludes_closed() -> None:
    reg = DynamicSlotRegistry()
    reg.add(_slot("a", state="running"))
    reg.add(_slot("b", item_id="2", state="closed"))
    assert len(reg.active_slots) == 1
    assert reg.active_slots[0].run_id == "a"


def test_registry_iteration() -> None:
    reg = DynamicSlotRegistry()
    reg.add(_slot("a"))
    reg.add(_slot("b", item_id="2"))
    run_ids = [s.run_id for s in reg]
    assert run_ids == ["a", "b"]


def test_registry_new_run_id_unique() -> None:
    ids = {DynamicSlotRegistry.new_run_id() for _ in range(100)}
    assert len(ids) == 100


def test_max_concurrent_implements_default() -> None:
    import os

    env_backup = os.environ.pop("COG_MAX_CONCURRENT_IMPLEMENTS", None)
    try:
        assert max_concurrent_implements() == 3
    finally:
        if env_backup is not None:
            os.environ["COG_MAX_CONCURRENT_IMPLEMENTS"] = env_backup


def test_max_concurrent_implements_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COG_MAX_CONCURRENT_IMPLEMENTS", "5")
    assert max_concurrent_implements() == 5


def test_max_concurrent_implements_minimum_one(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COG_MAX_CONCURRENT_IMPLEMENTS", "0")
    assert max_concurrent_implements() == 1


def test_max_concurrent_implements_bad_env_returns_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COG_MAX_CONCURRENT_IMPLEMENTS", "not-a-number")
    assert max_concurrent_implements() == 3
