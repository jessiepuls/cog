"""Tests for IssuesView workflow launch actions (#192).

Covers `r` (refine), `i` (implement), `ctrl+r` (refresh),
label-gating logic, and LaunchSlotRequest message posting.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from textual.app import App, ComposeResult

from cog.core.item import Item
from cog.ui.messages import LaunchSlotRequest
from cog.ui.views.issues import IssuesView, _launch_confirm_message, _recommended_workflow
from tests.fakes import FakeIssueTracker, make_item

_BASE_DT = datetime(2026, 1, 1, tzinfo=UTC)


def _item(item_id: str, labels: tuple[str, ...] = ()) -> Item:
    return make_item(item_id=item_id, labels=labels, updated_at=_BASE_DT)


class _IssuesApp(App):
    """Minimal test harness for IssuesView. Captures LaunchSlotRequest messages."""

    def __init__(self, tracker: FakeIssueTracker, project_dir: Path) -> None:
        super().__init__()
        self._tracker = tracker
        self._project_dir = project_dir
        self.issues_filter_query: str = "state:open"
        self.current_user_login: str | None = None
        self.launch_requests: list[LaunchSlotRequest] = []

    def compose(self) -> ComposeResult:
        yield IssuesView(self._project_dir, self._tracker)

    def on_mount(self) -> None:
        view = self.query_one(IssuesView)
        self.call_after_refresh(view.on_show)

    def on_launch_slot_request(self, msg: LaunchSlotRequest) -> None:
        self.launch_requests.append(msg)


# ---------------------------------------------------------------------------
# Label gating helpers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "labels,expected",
    [
        (("needs-refinement",), "refine"),
        (("agent-ready",), "implement"),
        ((), None),
        (("needs-refinement", "agent-ready"), "refine"),  # needs-refinement wins
    ],
)
def test_recommended_workflow(labels: tuple[str, ...], expected: str | None) -> None:
    item = _item("1", labels=labels)
    assert _recommended_workflow(item) == expected


@pytest.mark.parametrize(
    "workflow,labels,expect_none",
    [
        ("refine", ("needs-refinement",), True),  # recommended → no confirm
        ("implement", ("agent-ready",), True),  # recommended → no confirm
        ("implement", ("needs-refinement",), False),  # non-recommended → confirm
        ("refine", ("agent-ready",), False),  # non-recommended → confirm
        ("refine", (), False),  # no labels → confirm
        ("implement", (), False),  # no labels → confirm
    ],
)
def test_launch_confirm_message(workflow: str, labels: tuple[str, ...], expect_none: bool) -> None:
    item = _item("42", labels=labels)
    result = _launch_confirm_message(workflow, item)  # type: ignore[arg-type]
    if expect_none:
        assert result is None
    else:
        assert result is not None
        assert "#42" in result


def test_launch_confirm_message_mentions_correct_label_mismatch() -> None:
    item = _item("7", labels=("needs-refinement",))
    msg = _launch_confirm_message("implement", item)
    assert msg is not None
    assert "needs-refinement" in msg
    assert "agent-ready" in msg


# ---------------------------------------------------------------------------
# IssuesView action wiring
# ---------------------------------------------------------------------------


async def test_r_key_posts_refine_request_for_agent_ready_item_after_confirm(
    tmp_path: Path,
) -> None:
    """r on an agent-ready item → confirm shown (non-recommended)."""
    from cog.ui.screens.launch_confirm import LaunchConfirmScreen

    tracker = FakeIssueTracker([_item("1", labels=("agent-ready",))])
    async with _IssuesApp(tracker, tmp_path).run_test(headless=True) as pilot:
        await pilot.pause(0.3)
        view = pilot.app.query_one(IssuesView)
        view.query_one("IssueList").focus_list()
        await pilot.pause()
        await pilot.press("r")
        for _ in range(3):
            await pilot.pause()
        # Confirm modal should be visible
        modals = [s for s in pilot.app.screen_stack if isinstance(s, LaunchConfirmScreen)]
        assert len(modals) == 1


async def test_i_key_posts_implement_request_for_agent_ready_item(tmp_path: Path) -> None:
    """i on an agent-ready item → no confirm, posts LaunchSlotRequest directly."""
    tracker = FakeIssueTracker([_item("1", labels=("agent-ready",))])
    async with _IssuesApp(tracker, tmp_path).run_test(headless=True) as pilot:
        await pilot.pause(0.3)
        # Focus the list first
        view = pilot.app.query_one(IssuesView)
        view.query_one("IssueList").focus_list()
        await pilot.pause()
        await pilot.press("i")
        for _ in range(3):
            await pilot.pause()
        assert len(pilot.app.launch_requests) == 1
        req = pilot.app.launch_requests[0]
        assert req.workflow == "implement"
        assert req.item.item_id == "1"


async def test_r_key_posts_refine_request_for_needs_refinement_item(tmp_path: Path) -> None:
    """r on a needs-refinement item → no confirm, posts LaunchSlotRequest."""
    tracker = FakeIssueTracker([_item("2", labels=("needs-refinement",))])
    async with _IssuesApp(tracker, tmp_path).run_test(headless=True) as pilot:
        await pilot.pause(0.3)
        view = pilot.app.query_one(IssuesView)
        view.query_one("IssueList").focus_list()
        await pilot.pause()
        await pilot.press("r")
        for _ in range(3):
            await pilot.pause()
        assert len(pilot.app.launch_requests) == 1
        req = pilot.app.launch_requests[0]
        assert req.workflow == "refine"
        assert req.item.item_id == "2"


async def test_ctrl_r_triggers_refresh(tmp_path: Path) -> None:
    tracker = FakeIssueTracker([_item("1")])
    async with _IssuesApp(tracker, tmp_path).run_test(headless=True) as pilot:
        await pilot.pause(0.3)
        view = pilot.app.query_one(IssuesView)
        view.query_one("IssueList").focus_list()
        await pilot.pause()
        initial_calls = len(tracker.list_calls)
        await pilot.press("ctrl+r")
        await pilot.pause(0.3)
        assert len(tracker.list_calls) > initial_calls


async def test_r_key_without_selection_does_nothing(tmp_path: Path) -> None:
    """r with no focused item should not crash or post a request."""
    tracker = FakeIssueTracker([])
    async with _IssuesApp(tracker, tmp_path).run_test(headless=True) as pilot:
        await pilot.pause(0.3)
        await pilot.press("r")
        await pilot.pause()
        assert len(pilot.app.launch_requests) == 0


async def test_i_key_without_selection_does_nothing(tmp_path: Path) -> None:
    tracker = FakeIssueTracker([])
    async with _IssuesApp(tracker, tmp_path).run_test(headless=True) as pilot:
        await pilot.pause(0.3)
        await pilot.press("i")
        await pilot.pause()
        assert len(pilot.app.launch_requests) == 0


async def test_r_key_in_filter_input_does_not_launch(tmp_path: Path) -> None:
    """r should not trigger launch when the filter input has focus."""
    tracker = FakeIssueTracker([_item("1", labels=("needs-refinement",))])
    async with _IssuesApp(tracker, tmp_path).run_test(headless=True) as pilot:
        await pilot.pause(0.3)
        # Focus the filter input
        from textual.widgets import Input

        pilot.app.query_one("#issues-filter-input", Input).focus()
        await pilot.pause()
        await pilot.press("r")
        await pilot.pause()
        assert len(pilot.app.launch_requests) == 0


async def test_non_recommended_confirm_enter_posts_request(tmp_path: Path) -> None:
    """After confirm modal (Enter), LaunchSlotRequest is posted."""
    from cog.ui.screens.launch_confirm import LaunchConfirmScreen

    tracker = FakeIssueTracker([_item("3", labels=("needs-refinement",))])
    async with _IssuesApp(tracker, tmp_path).run_test(headless=True) as pilot:
        await pilot.pause(0.3)
        view = pilot.app.query_one(IssuesView)
        view.query_one("IssueList").focus_list()
        await pilot.pause()
        # i on needs-refinement → confirm
        await pilot.press("i")
        for _ in range(3):
            await pilot.pause()
        modals = [s for s in pilot.app.screen_stack if isinstance(s, LaunchConfirmScreen)]
        assert modals
        await pilot.press("enter")
        for _ in range(3):
            await pilot.pause()
        assert len(pilot.app.launch_requests) == 1
        assert pilot.app.launch_requests[0].workflow == "implement"


async def test_non_recommended_confirm_escape_cancels(tmp_path: Path) -> None:
    from cog.ui.screens.launch_confirm import LaunchConfirmScreen

    tracker = FakeIssueTracker([_item("3", labels=("needs-refinement",))])
    async with _IssuesApp(tracker, tmp_path).run_test(headless=True) as pilot:
        await pilot.pause(0.3)
        view = pilot.app.query_one(IssuesView)
        view.query_one("IssueList").focus_list()
        await pilot.pause()
        await pilot.press("i")
        for _ in range(3):
            await pilot.pause()
        modals = [s for s in pilot.app.screen_stack if isinstance(s, LaunchConfirmScreen)]
        assert modals
        await pilot.press("escape")
        for _ in range(3):
            await pilot.pause()
        assert len(pilot.app.launch_requests) == 0
