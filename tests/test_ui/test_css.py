"""Verify cog.tcss loads without parse errors."""


async def test_cog_tcss_parses_without_error() -> None:
    """CogApp should be able to mount without CSS parse errors."""
    from textual.screen import Screen

    from cog.ui.app import CogApp

    class _EmptyScreen(Screen):
        def compose(self):
            return iter([])

    app = CogApp(_EmptyScreen())
    async with app.run_test(headless=True):
        pass  # CSS parse error would raise here
