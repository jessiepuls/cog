"""Tests for CogShellScreen dynamic slot features (#192).

Covers sidebar divider, dynamic rows, slot dismissal, quit guard,
and ctrl+6..N keyboard navigation.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock

from textual.app import App
from textual.widgets import ListView

from cog.core.tracker import IssueTracker
from cog.ui.dynamic_slots import DynamicSlot
from cog.ui.messages import SlotDismissed, SlotStateChanged
from cog.ui.screens.shell import _STATIC_COUNT, CogShellScreen, Sidebar

_BASE_DT = datetime(2026, 1, 1, tzinfo=UTC)


def _fake_tracker() -> IssueTracker:
    t = AsyncMock(spec=IssueTracker)
    t.list_by_label = AsyncMock(return_value=[])
    return t  # type: ignore[return-value]


def _slot(
    run_id: str = "abc",
    workflow: str = "implement",
    item_id: str = "1",
    state: str = "running",
    stage: str = "build",
) -> DynamicSlot:
    return DynamicSlot(run_id=run_id, workflow=workflow, item_id=item_id, state=state, stage=stage)  # type: ignore[arg-type]


class _ShellApp(App):
    def __init__(self, project_dir: Path, tracker: IssueTracker | None = None) -> None:
        super().__init__()
        self._project_dir = project_dir
        self._tracker = tracker or _fake_tracker()

    def on_mount(self) -> None:
        self.push_screen(CogShellScreen(self._project_dir, self._tracker))


# ---------------------------------------------------------------------------
# Static sidebar unchanged
# ---------------------------------------------------------------------------


async def test_static_sidebar_row_count(tmp_path: Path) -> None:
    async with _ShellApp(tmp_path).run_test(headless=True) as pilot:
        await pilot.pause()
        list_view = pilot.app.query_one("#sidebar-nav", ListView)
        assert len(list(list_view.children)) == _STATIC_COUNT


# ---------------------------------------------------------------------------
# Dynamic slot sidebar rows
# ---------------------------------------------------------------------------


async def test_dynamic_slot_adds_divider_and_row(tmp_path: Path) -> None:
    async with _ShellApp(tmp_path).run_test(headless=True) as pilot:
        await pilot.pause()
        screen = pilot.app.screen
        assert isinstance(screen, CogShellScreen)
        sidebar = pilot.app.query_one(Sidebar)
        slot = _slot("run1", item_id="42", stage="build")

        await sidebar.update_dynamic_slots([slot])
        await pilot.pause()

        list_view = pilot.app.query_one("#sidebar-nav", ListView)
        children = list(list_view.children)
        # static + divider + 1 slot
        assert len(children) == _STATIC_COUNT + 2
        divider = next((c for c in children if c.id == "nav-divider"), None)
        assert divider is not None
        slot_row = next((c for c in children if c.id == "nav-slot-run1"), None)
        assert slot_row is not None


async def test_dynamic_slot_row_label_format(tmp_path: Path) -> None:
    async with _ShellApp(tmp_path).run_test(headless=True) as pilot:
        await pilot.pause()
        sidebar = pilot.app.query_one(Sidebar)
        slot = _slot("run1", workflow="implement", item_id="99", stage="review")

        await sidebar.update_dynamic_slots([slot])
        await pilot.pause()

        list_view = pilot.app.query_one("#sidebar-nav", ListView)
        slot_row = next(c for c in list_view.children if c.id == "nav-slot-run1")
        label_text = str(slot_row.query_one("Label").renderable)
        assert "I" in label_text  # implement prefix
        assert "#99" in label_text
        assert "review" in label_text
        keybind_n = _STATIC_COUNT + 1
        assert f"^{keybind_n}" in label_text


async def test_divider_not_shown_when_no_dynamic_slots(tmp_path: Path) -> None:
    async with _ShellApp(tmp_path).run_test(headless=True) as pilot:
        await pilot.pause()
        sidebar = pilot.app.query_one(Sidebar)

        await sidebar.update_dynamic_slots([])
        await pilot.pause()

        list_view = pilot.app.query_one("#sidebar-nav", ListView)
        divider = next((c for c in list_view.children if c.id == "nav-divider"), None)
        assert divider is None


async def test_dynamic_slots_renumbered_after_removal(tmp_path: Path) -> None:
    """After removing one slot, remaining slot gets a lower Ctrl-N number."""
    async with _ShellApp(tmp_path).run_test(headless=True) as pilot:
        await pilot.pause()
        sidebar = pilot.app.query_one(Sidebar)
        # Two slots
        await sidebar.update_dynamic_slots(
            [
                _slot("r1", item_id="1"),
                _slot("r2", item_id="2"),
            ]
        )
        await pilot.pause()
        # Remove first slot → r2 should now be at ctrl+6
        await sidebar.update_dynamic_slots([_slot("r2", item_id="2")])
        await pilot.pause()

        list_view = pilot.app.query_one("#sidebar-nav", ListView)
        slot_row = next(c for c in list_view.children if c.id == "nav-slot-r2")
        label_text = str(slot_row.query_one("Label").renderable)
        assert f"^{_STATIC_COUNT + 1}" in label_text


# ---------------------------------------------------------------------------
# SlotDismissed message handling
# ---------------------------------------------------------------------------


async def test_slot_dismissed_removes_registry_entry(tmp_path: Path) -> None:
    async with _ShellApp(tmp_path).run_test(headless=True) as pilot:
        await pilot.pause()
        screen = pilot.app.screen
        assert isinstance(screen, CogShellScreen)
        slot = _slot("run1")
        screen._registry.add(slot)

        screen.post_message(SlotDismissed("run1"))
        for _ in range(3):
            await pilot.pause()

        assert screen._registry.get_by_run_id("run1") is None


# ---------------------------------------------------------------------------
# SlotStateChanged updates registry
# ---------------------------------------------------------------------------


async def test_slot_state_changed_updates_registry(tmp_path: Path) -> None:
    async with _ShellApp(tmp_path).run_test(headless=True) as pilot:
        await pilot.pause()
        screen = pilot.app.screen
        assert isinstance(screen, CogShellScreen)
        slot = _slot("run1", state="running")
        screen._registry.add(slot)

        screen.post_message(SlotStateChanged("run1", "awaiting_dismiss", "build"))  # type: ignore[arg-type]
        for _ in range(3):
            await pilot.pause()

        updated = screen._registry.get_by_run_id("run1")
        assert updated is not None
        assert updated.state == "awaiting_dismiss"


# ---------------------------------------------------------------------------
# Ctrl+N navigation
# ---------------------------------------------------------------------------


async def test_ctrl_6_activates_first_dynamic_slot(tmp_path: Path) -> None:
    async with _ShellApp(tmp_path).run_test(headless=True) as pilot:
        await pilot.pause()
        screen = pilot.app.screen
        assert isinstance(screen, CogShellScreen)
        # Manually add a slot to registry and mount a fake view
        from textual.widgets import Static

        slot = _slot("run1", item_id="42")
        screen._registry.add(slot)
        fake_view = Static("slot content", id="view-slot-run1")
        content_area = pilot.app.query_one("#content-area")
        await content_area.mount(fake_view)
        fake_view.display = False

        await pilot.press(f"ctrl+{_STATIC_COUNT + 1}")
        for _ in range(3):
            await pilot.pause()

        assert screen._active_view_id == "slot-run1"
        assert fake_view.display is True


async def test_ctrl_static_N_not_intercepted_by_on_key(tmp_path: Path) -> None:
    """ctrl+1..5 should still work — not swallowed by the dynamic key handler."""
    async with _ShellApp(tmp_path).run_test(headless=True) as pilot:
        await pilot.pause()
        await pilot.press("ctrl+2")
        await pilot.pause()
        screen = pilot.app.screen
        assert isinstance(screen, CogShellScreen)
        assert screen._active_view_id == "issues"


# ---------------------------------------------------------------------------
# Quit guard with active dynamic slots
# ---------------------------------------------------------------------------


async def test_quit_guard_counts_active_dynamic_slots(tmp_path: Path) -> None:
    from cog.ui.screens.quit_confirm import QuitConfirmScreen

    async with _ShellApp(tmp_path).run_test(headless=True) as pilot:
        await pilot.pause()
        screen = pilot.app.screen
        assert isinstance(screen, CogShellScreen)
        # Add a running dynamic slot (no busy_description widget needed —
        # the shell checks active_count() directly now)
        slot = _slot("run1", state="running")
        screen._registry.add(slot)

        await pilot.press("ctrl+q")
        for _ in range(3):
            await pilot.pause()

        assert pilot.app.is_running
        modals = [s for s in pilot.app.screen_stack if isinstance(s, QuitConfirmScreen)]
        assert len(modals) == 1


async def test_quit_guard_includes_awaiting_dismiss_slots(tmp_path: Path) -> None:
    from cog.ui.screens.quit_confirm import QuitConfirmScreen

    async with _ShellApp(tmp_path).run_test(headless=True) as pilot:
        await pilot.pause()
        screen = pilot.app.screen
        assert isinstance(screen, CogShellScreen)
        slot = _slot("run1", state="awaiting_dismiss")
        screen._registry.add(slot)

        await pilot.press("ctrl+q")
        for _ in range(3):
            await pilot.pause()

        modals = [s for s in pilot.app.screen_stack if isinstance(s, QuitConfirmScreen)]
        assert len(modals) == 1


async def test_quit_no_guard_when_all_slots_closed(tmp_path: Path) -> None:
    async with _ShellApp(tmp_path).run_test(headless=True) as pilot:
        await pilot.pause()
        screen = pilot.app.screen
        assert isinstance(screen, CogShellScreen)
        slot = _slot("run1", state="closed")
        screen._registry.add(slot)

        await pilot.press("ctrl+q")
        for _ in range(3):
            await pilot.pause()

        # App should exit since no active (running/awaiting) slots
        assert not pilot.app.is_running


# ---------------------------------------------------------------------------
# Launch dedup + concurrency cap
# ---------------------------------------------------------------------------


def _fake_item(item_id: str = "1"):
    from tests.fakes import make_item

    return make_item(item_id=item_id, updated_at=_BASE_DT)


async def test_dedup_existing_slot_focuses_instead_of_spawning(tmp_path: Path) -> None:
    """Pressing the launch key for an item with an existing slot does not spawn a duplicate."""
    from textual.widgets import Static

    from cog.ui.messages import LaunchSlotRequest

    async with _ShellApp(tmp_path).run_test(headless=True) as pilot:
        await pilot.pause()
        screen = pilot.app.screen
        assert isinstance(screen, CogShellScreen)

        # Pre-populate registry with an existing implement slot for item 42
        existing = _slot("existing", workflow="implement", item_id="42")
        screen._registry.add(existing)
        # Mount a stand-in view so _switch_to_slot doesn't trip on a missing widget
        content = pilot.app.query_one("#content-area")
        await content.mount(Static("placeholder", id="view-slot-existing"))

        # Request another implement run on the same item
        screen.post_message(LaunchSlotRequest("implement", _fake_item("42")))
        for _ in range(5):
            await pilot.pause()

        # Registry still has only the original slot
        assert screen._registry.active_count("implement") == 1
        # Active view switched to the existing slot
        assert screen._active_view_id == "slot-existing"


async def test_concurrency_cap_refuses_extra_implement(tmp_path: Path, monkeypatch) -> None:
    """Pressing `i` past the implement cap notifies and refuses to spawn."""
    from cog.ui.messages import LaunchSlotRequest

    monkeypatch.setenv("COG_MAX_CONCURRENT_IMPLEMENTS", "2")

    async with _ShellApp(tmp_path).run_test(headless=True) as pilot:
        await pilot.pause()
        screen = pilot.app.screen
        assert isinstance(screen, CogShellScreen)

        # Fill the cap with two active implement slots
        screen._registry.add(_slot("a", workflow="implement", item_id="1"))
        screen._registry.add(_slot("b", workflow="implement", item_id="2"))

        notifications: list[str] = []
        original_notify = pilot.app.notify

        def capture_notify(message: str, **kwargs):  # type: ignore[no-untyped-def]
            notifications.append(message)
            return original_notify(message, **kwargs)

        pilot.app.notify = capture_notify  # type: ignore[method-assign]

        # Third implement should be refused
        screen.post_message(LaunchSlotRequest("implement", _fake_item("3")))
        for _ in range(5):
            await pilot.pause()

        assert screen._registry.active_count("implement") == 2
        assert any("dismiss one" in n for n in notifications)


async def test_cap_does_not_apply_to_refine(tmp_path: Path, monkeypatch) -> None:
    """The cap is implement-only — refine is uncapped."""
    from cog.ui.messages import LaunchSlotRequest
    from cog.ui.widgets.dynamic_slot_view import DynamicSlotView

    monkeypatch.setenv("COG_MAX_CONCURRENT_IMPLEMENTS", "1")
    # Prevent start_run from launching a real subprocess in the test process.
    monkeypatch.setattr(DynamicSlotView, "start_run", lambda self: None)

    async with _ShellApp(tmp_path).run_test(headless=True) as pilot:
        await pilot.pause()
        screen = pilot.app.screen
        assert isinstance(screen, CogShellScreen)

        # An implement at cap should not block refines
        screen._registry.add(_slot("imp", workflow="implement", item_id="1"))

        screen.post_message(LaunchSlotRequest("refine", _fake_item("99")))
        for _ in range(5):
            await pilot.pause()

        # Cap-refusal is implement-only; refine slot was created, not refused.
        assert screen._registry.active_count("implement") == 1
        assert screen._registry.active_count("refine") == 1
