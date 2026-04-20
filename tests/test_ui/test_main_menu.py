"""Tests for MainMenuScreen."""

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from textual.app import App, ComposeResult
from textual.widgets import ListView

from cog.core.item import Item
from cog.core.workflow import Workflow
from cog.ui.screens.main_menu import MainMenuScreen
from tests.fakes import NullContentWidget


def _item(n: int) -> Item:
    return Item(
        tracker_id="gh",
        item_id=str(n),
        title=f"item {n}",
        body="",
        labels=(),
        comments=(),
        state="open",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        url="",
    )


class _FakeTracker:
    def __init__(self, items: list[Item] | None = None) -> None:
        self._items = items or []
        self.list_by_label_calls: list[tuple[str, str | None]] = []

    async def list_by_label(self, label: str, *, assignee: str | None = None) -> list[Item]:
        self.list_by_label_calls.append((label, assignee))
        return self._items

    async def get(self, item_id: str) -> Item:  # pragma: no cover
        return self._items[0]

    async def comment(self, item: Item, body: str) -> None:  # pragma: no cover
        pass

    async def add_label(self, item: Item, label: str) -> None:  # pragma: no cover
        pass

    async def remove_label(self, item: Item, label: str) -> None:  # pragma: no cover
        pass

    async def update_body(
        self, item: Item, body: str, *, title: str | None = None
    ) -> None:  # pragma: no cover
        pass


class _FakeWorkflow(Workflow):
    name = "fake"
    queue_label = "fake-label"
    supports_headless = True
    content_widget_cls = NullContentWidget

    async def select_item(self, ctx):  # type: ignore[override]
        return None

    def stages(self, ctx):
        return []

    async def classify_outcome(self, ctx, results):
        return "noop"


def _make_app(tracker: _FakeTracker) -> App:
    class _TestApp(App):
        def compose(self) -> ComposeResult:
            return iter([])

        def on_mount(self) -> None:
            self.push_screen(
                MainMenuScreen(Path("/tmp"), tracker)  # noqa: S108
            )

    return _TestApp()


async def test_main_menu_lists_workflows() -> None:
    tracker = _FakeTracker()
    with patch("cog.ui.screens.main_menu.WORKFLOWS", [_FakeWorkflow]):
        async with _make_app(tracker).run_test(headless=True) as pilot:
            await pilot.pause()
            list_view = pilot.app.query_one("#workflows", ListView)
            assert len(list_view) == 1


async def test_main_menu_labels_show_queue_counts() -> None:
    tracker = _FakeTracker(items=[_item(1), _item(2)])
    with patch("cog.ui.screens.main_menu.WORKFLOWS", [_FakeWorkflow]):
        async with _make_app(tracker).run_test(headless=True) as pilot:
            await pilot.pause()
            list_view = pilot.app.query_one("#workflows", ListView)
            rendered = list_view.query("Label").first().renderable
            assert "2" in str(rendered)


async def test_main_menu_tracker_error_shows_question_mark_placeholder() -> None:
    class _ErrorTracker(_FakeTracker):
        async def list_by_label(self, label: str, *, assignee: str | None = None) -> list[Item]:
            raise RuntimeError("gh CLI unavailable")

    tracker = _ErrorTracker()
    with patch("cog.ui.screens.main_menu.WORKFLOWS", [_FakeWorkflow]):
        async with _make_app(tracker).run_test(headless=True) as pilot:
            await pilot.pause()
            list_view = pilot.app.query_one("#workflows", ListView)
            rendered = str(list_view.query("Label").first().renderable)
            assert "?" in rendered


async def test_main_menu_q_quits() -> None:
    tracker = _FakeTracker()
    with patch("cog.ui.screens.main_menu.WORKFLOWS", []):
        async with _make_app(tracker).run_test(headless=True) as pilot:
            await pilot.pause()
            await pilot.press("q")
            assert not pilot.app.is_running


def test_main_menu_uses_populated_workflow_registry() -> None:
    # Regression guard: MainMenuScreen imports WORKFLOWS from the real registry.
    from cog.ui.screens import main_menu
    from cog.workflows import WORKFLOWS as registry
    from cog.workflows import RalphWorkflow

    assert main_menu.WORKFLOWS is registry
    assert RalphWorkflow in main_menu.WORKFLOWS


async def test_main_menu_refreshes_counts_on_screen_resume() -> None:
    tracker = _FakeTracker(items=[_item(1)])
    initial_calls = [0]

    with patch("cog.ui.screens.main_menu.WORKFLOWS", [_FakeWorkflow]):
        async with _make_app(tracker).run_test(headless=True) as pilot:
            await pilot.pause()
            initial_calls[0] = len(tracker.list_by_label_calls)

            # Push a dummy screen then pop it to trigger on_screen_resume
            from textual.screen import Screen as TScreen

            class _Dummy(TScreen):
                def compose(self) -> ComposeResult:
                    return iter([])

            await pilot.app.push_screen(_Dummy())
            await pilot.pause()
            pilot.app.pop_screen()
            await pilot.pause()

    assert len(tracker.list_by_label_calls) > initial_calls[0]


async def test_main_menu_r_key_refreshes_counts() -> None:
    tracker = _FakeTracker(items=[_item(1)])

    with patch("cog.ui.screens.main_menu.WORKFLOWS", [_FakeWorkflow]):
        async with _make_app(tracker).run_test(headless=True) as pilot:
            await pilot.pause()
            before = len(tracker.list_by_label_calls)
            await pilot.press("r")
            await pilot.pause()

    assert len(tracker.list_by_label_calls) > before


async def test_main_menu_selection_pushes_preflight_then_picker_then_run_screen() -> None:
    fake_item = _item(1)
    fake_run_screen = MagicMock()
    push_order: list[str] = []

    class _SentinelPreflight:
        pass

    class _SentinelPicker:
        pass

    async def fake_push_screen_wait(screen):
        push_order.append(type(screen).__name__)
        if isinstance(screen, _SentinelPreflight):
            return True
        return fake_item

    async def fake_push_screen(screen):
        push_order.append(type(screen).__name__)

    tracker = _FakeTracker(items=[fake_item])
    with (
        patch("cog.ui.screens.main_menu.WORKFLOWS", [_FakeWorkflow]),
        patch("cog.ui.preflight.PreflightScreen", return_value=_SentinelPreflight()),
        patch("cog.ui.picker.PickerScreen", return_value=_SentinelPicker()),
        patch("cog.ui.wire.build_run_screen", new=AsyncMock(return_value=fake_run_screen)),
    ):
        async with _make_app(tracker).run_test(headless=True) as pilot:
            pilot.app.push_screen_wait = fake_push_screen_wait  # type: ignore[method-assign]
            pilot.app.push_screen = fake_push_screen  # type: ignore[method-assign]
            await pilot.pause()
            list_view = pilot.app.query_one("#workflows", ListView)
            list_view.focus()
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()

    assert "_SentinelPreflight" in push_order
    assert "_SentinelPicker" in push_order
    assert "MagicMock" in push_order


async def test_main_menu_preflight_fails_does_not_show_picker() -> None:
    fake_item = _item(1)
    picker_shown = [False]

    class _SentinelPreflight:
        pass

    class _SentinelPicker:
        pass

    async def fake_push_screen_wait(screen):
        if isinstance(screen, _SentinelPreflight):
            return False  # preflight failed
        picker_shown[0] = True
        return fake_item

    tracker = _FakeTracker(items=[fake_item])
    with (
        patch("cog.ui.screens.main_menu.WORKFLOWS", [_FakeWorkflow]),
        patch("cog.ui.preflight.PreflightScreen", return_value=_SentinelPreflight()),
        patch("cog.ui.picker.PickerScreen", return_value=_SentinelPicker()),
        patch("cog.ui.wire.build_run_screen", new=AsyncMock(return_value=MagicMock())),
    ):
        async with _make_app(tracker).run_test(headless=True) as pilot:
            pilot.app.push_screen_wait = fake_push_screen_wait  # type: ignore[method-assign]
            await pilot.pause()
            list_view = pilot.app.query_one("#workflows", ListView)
            list_view.focus()
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()

    assert not picker_shown[0]


async def test_main_menu_selection_dispatches_to_worker() -> None:
    # Regression: on_list_view_selected calls push_screen_wait, which requires
    # worker context. The message handler itself is not a worker, so the flow
    # must be dispatched via run_worker or Textual raises NoActiveWorker.
    captured: list[object] = []

    def capture(coro, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003, ARG001
        captured.append(coro)
        coro.close()  # suppress "coroutine was never awaited" warning
        return MagicMock()

    tracker = _FakeTracker(items=[_item(1)])
    with patch("cog.ui.screens.main_menu.WORKFLOWS", [_FakeWorkflow]):
        async with _make_app(tracker).run_test(headless=True) as pilot:
            await pilot.pause()
            screen = pilot.app.screen
            assert isinstance(screen, MainMenuScreen)
            with patch.object(screen, "run_worker", side_effect=capture):
                list_view = pilot.app.query_one("#workflows", ListView)
                list_view.focus()
                await pilot.pause()
                await pilot.press("enter")
                await pilot.pause()
            assert captured, (
                "on_list_view_selected must dispatch via run_worker; "
                "calling push_screen_wait directly from a message handler raises NoActiveWorker"
            )


async def test_main_menu_picker_cancel_does_not_push_run_screen() -> None:
    fake_item = _item(1)
    run_screen_pushed = [False]

    class _SentinelPreflight:
        pass

    class _SentinelPicker:
        pass

    async def fake_push_screen_wait(screen):
        if isinstance(screen, _SentinelPreflight):
            return True
        return None  # picker cancelled

    async def fake_push_screen(screen):
        run_screen_pushed[0] = True

    tracker = _FakeTracker(items=[fake_item])
    with (
        patch("cog.ui.screens.main_menu.WORKFLOWS", [_FakeWorkflow]),
        patch("cog.ui.preflight.PreflightScreen", return_value=_SentinelPreflight()),
        patch("cog.ui.picker.PickerScreen", return_value=_SentinelPicker()),
        patch("cog.ui.wire.build_run_screen", new=AsyncMock(return_value=MagicMock())),
    ):
        async with _make_app(tracker).run_test(headless=True) as pilot:
            pilot.app.push_screen_wait = fake_push_screen_wait  # type: ignore[method-assign]
            pilot.app.push_screen = fake_push_screen  # type: ignore[method-assign]
            await pilot.pause()
            list_view = pilot.app.query_one("#workflows", ListView)
            list_view.focus()
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()

    assert not run_screen_pushed[0]
