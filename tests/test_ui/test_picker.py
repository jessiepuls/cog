"""Tests for PickerScreen, OtherInputScreen, and TextualItemPicker."""

from unittest.mock import AsyncMock

from textual.app import App
from textual.widgets import Input, Label

from cog.core.errors import TrackerError
from cog.core.item import Item
from cog.core.tracker import IssueTracker
from cog.ui.picker import OtherInputScreen, PickerScreen
from tests.fakes import make_item, make_needs_refinement_items


class _PickerApp(App):
    """Minimal app that pushes a screen on mount for testing."""

    def __init__(self, screen: PickerScreen | OtherInputScreen) -> None:
        super().__init__()
        self._screen = screen
        self.result: Item | None = None
        self._done = False

    def on_mount(self) -> None:
        self.push_screen(self._screen, callback=self._capture)

    def _capture(self, result: Item | None) -> None:
        self.result = result
        self._done = True
        self.exit()


def _make_items(n: int) -> list[Item]:
    return make_needs_refinement_items(list(range(1, n + 1)))


async def _set_input_value(pilot, selector: str, value: str) -> None:
    """Set the value of an Input widget directly and focus it."""
    inp = pilot.app.query_one(selector, Input)
    inp.value = value
    inp.focus()


async def test_picker_screen_lists_top_4_with_titles():
    items = _make_items(5)
    tracker = AsyncMock(spec=IssueTracker)
    screen = PickerScreen(items, tracker)
    app = _PickerApp(screen)
    async with app.run_test() as _:
        list_view = app.query_one("#picker-list")
        # 4 items + 1 "Other" = 5 children
        assert len(list_view.children) == 5


async def test_picker_screen_fifth_option_is_other():
    items = _make_items(5)
    tracker = AsyncMock(spec=IssueTracker)
    screen = PickerScreen(items, tracker)
    app = _PickerApp(screen)
    async with app.run_test() as _:
        list_view = app.query_one("#picker-list")
        last = list_view.children[-1]
        assert last.id == "pick-other"


async def test_picker_screen_enter_dismisses_with_selected_item():
    items = _make_items(3)
    tracker = AsyncMock(spec=IssueTracker)
    screen = PickerScreen(items, tracker)
    app = _PickerApp(screen)
    async with app.run_test() as pilot:
        await pilot.press("enter")
    assert app.result == items[0]


async def test_picker_screen_arrow_navigation():
    items = _make_items(3)
    tracker = AsyncMock(spec=IssueTracker)
    screen = PickerScreen(items, tracker)
    app = _PickerApp(screen)
    async with app.run_test() as pilot:
        await pilot.press("down")
        await pilot.press("enter")
    assert app.result == items[1]


async def test_picker_screen_q_dismisses_with_none():
    items = _make_items(2)
    tracker = AsyncMock(spec=IssueTracker)
    screen = PickerScreen(items, tracker)
    app = _PickerApp(screen)
    async with app.run_test() as pilot:
        await pilot.press("q")
    assert app.result is None


async def test_title_truncation_with_ellipsis():
    long_title = "A" * 100
    item = make_item(item_id="99", title=long_title)
    tracker = AsyncMock(spec=IssueTracker)
    screen = PickerScreen([item], tracker)
    app = _PickerApp(screen)
    async with app.run_test() as _:
        list_view = app.query_one("#picker-list")
        first_item = list_view.children[0]
        label = first_item.query_one(Label)
        rendered = str(label.renderable)
        assert "…" in rendered
        assert len(rendered) < len(long_title) + 20


async def test_other_input_screen_valid_input_fetches_and_dismisses():
    expected = make_item(item_id="42", title="fetched item")
    tracker = AsyncMock(spec=IssueTracker)
    tracker.get = AsyncMock(return_value=expected)
    screen = OtherInputScreen(tracker)
    app = _PickerApp(screen)
    async with app.run_test() as pilot:
        await _set_input_value(pilot, "#other-input", "42")
        await pilot.press("enter")
    assert app.result == expected
    tracker.get.assert_awaited_once_with("42")


async def test_other_input_screen_hash_prefix_stripped():
    expected = make_item(item_id="7", title="issue 7")
    tracker = AsyncMock(spec=IssueTracker)
    tracker.get = AsyncMock(return_value=expected)
    screen = OtherInputScreen(tracker)
    app = _PickerApp(screen)
    async with app.run_test() as pilot:
        await _set_input_value(pilot, "#other-input", "#7")
        await pilot.press("enter")
    assert app.result == expected
    tracker.get.assert_awaited_once_with("7")


async def test_other_input_screen_non_numeric_shows_error_stays():
    tracker = AsyncMock(spec=IssueTracker)
    screen = OtherInputScreen(tracker)
    app = _PickerApp(screen)
    async with app.run_test() as pilot:
        await _set_input_value(pilot, "#other-input", "abc")
        await pilot.press("enter")
        await pilot.pause()
        error_label = app.query_one("#other-error", Label)
        assert "not a number" in str(error_label.renderable)
        assert not app._done


async def test_other_input_screen_tracker_error_shows_error_clears_input():
    tracker = AsyncMock(spec=IssueTracker)
    tracker.get = AsyncMock(side_effect=TrackerError("not found"))
    screen = OtherInputScreen(tracker)
    app = _PickerApp(screen)
    async with app.run_test() as pilot:
        await _set_input_value(pilot, "#other-input", "99")
        await pilot.press("enter")
        await pilot.pause()
        inp = app.query_one("#other-input", Input)
        error_label = app.query_one("#other-error", Label)
        assert inp.value == ""
        assert "Could not fetch" in str(error_label.renderable)
        assert not app._done


async def test_other_input_screen_q_dismisses_with_none():
    tracker = AsyncMock(spec=IssueTracker)
    screen = OtherInputScreen(tracker)
    app = _PickerApp(screen)
    async with app.run_test() as pilot:
        await pilot.press("q")
    assert app.result is None


async def test_other_input_screen_escape_dismisses_with_none():
    tracker = AsyncMock(spec=IssueTracker)
    screen = OtherInputScreen(tracker)
    app = _PickerApp(screen)
    async with app.run_test() as pilot:
        await pilot.press("escape")
    assert app.result is None


async def test_picker_screen_selecting_other_pushes_input_screen():
    items = _make_items(2)
    expected = make_item(item_id="55", title="other item")
    tracker = AsyncMock(spec=IssueTracker)
    tracker.get = AsyncMock(return_value=expected)
    screen = PickerScreen(items, tracker)
    app = _PickerApp(screen)
    async with app.run_test() as pilot:
        # Navigate to "Other" — it's the last item (index 2 for 2 items)
        await pilot.press("down")
        await pilot.press("down")
        await pilot.press("enter")
        # Now OtherInputScreen is on top
        await _set_input_value(pilot, "#other-input", "55")
        await pilot.press("enter")
    assert app.result == expected
