"""Tests for MainMenuScreen."""

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

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
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        url="",
    )


class _FakeTracker:
    def __init__(self, items: list[Item] | None = None) -> None:
        self._items = items or []

    async def list_by_label(self, label: str, *, assignee: str | None = None) -> list[Item]:
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


async def test_main_menu_enter_pushes_run_screen() -> None:
    from textual.screen import Screen

    pushed: list[Screen] = []

    class _Sentinel(Screen):
        def compose(self) -> ComposeResult:
            return iter([])

    def _factory(cls: type[Workflow]) -> Screen:
        s = _Sentinel()
        pushed.append(s)
        return s

    tracker = _FakeTracker(items=[_item(1)])
    with patch("cog.ui.screens.main_menu.WORKFLOWS", [_FakeWorkflow]):
        screen = MainMenuScreen(Path("/tmp"), tracker, run_screen_factory=_factory)  # noqa: S108

        class _TestApp(App):
            def compose(self) -> ComposeResult:
                return iter([])

            def on_mount(self) -> None:
                self.push_screen(screen)

        async with _TestApp().run_test(headless=True) as pilot:
            await pilot.pause()
            # Ensure the ListView is focused so Enter triggers action_select_cursor
            list_view = pilot.app.query_one("#workflows", ListView)
            list_view.focus()
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            assert len(pushed) == 1
