"""Tests for TextualItemPicker adapter."""

from unittest.mock import AsyncMock, MagicMock

from cog.ui.picker import TextualItemPicker
from tests.fakes import make_item


async def test_textual_item_picker_delegates_to_push_screen_wait():
    expected = make_item(item_id="1")
    app = MagicMock()
    app.push_screen_wait = AsyncMock(return_value=expected)
    tracker = MagicMock()

    picker = TextualItemPicker(app, tracker)
    items = [expected]
    result = await picker.pick(items)

    assert result == expected
    app.push_screen_wait.assert_awaited_once()
    # Verify the screen passed was a PickerScreen
    from cog.ui.picker import PickerScreen

    args = app.push_screen_wait.call_args[0]
    assert isinstance(args[0], PickerScreen)
