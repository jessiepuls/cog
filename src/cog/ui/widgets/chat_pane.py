"""Multi-turn chat widget for interactive workflows (e.g. refine)."""

import asyncio

from textual.app import ComposeResult
from textual.events import Key
from textual.widget import Widget
from textual.widgets import RichLog, Static, TextArea

from cog.core.runner import AssistantTextEvent, ResultEvent, RunEvent


class ChatPaneWidget(Widget):
    """Multi-turn chat with Claude. Implements RunEventSink + UserInputProvider."""

    DEFAULT_CSS = """
    ChatPaneWidget {
        height: 1fr;
        border: solid $accent;
        layout: vertical;
    }
    ChatPaneWidget #scrollback {
        height: 1fr;
    }
    ChatPaneWidget #thinking {
        height: 1;
        color: $text-muted;
    }
    ChatPaneWidget #input-area {
        height: 5;
        border: solid $primary;
    }
    """

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        self._input_future: asyncio.Future[str] = asyncio.get_event_loop().create_future()

    def compose(self) -> ComposeResult:
        yield RichLog(id="scrollback", highlight=True, markup=True)
        yield Static("⏳ Thinking…", id="thinking")
        yield TextArea(id="input-area")

    def on_mount(self) -> None:
        self.query_one("#thinking", Static).display = False

    def _append_assistant_message(self, text: str) -> None:
        log = self.query_one("#scrollback", RichLog)
        log.write(f"[bold blue]Claude:[/bold blue] {text}")
        log.scroll_end(animate=False)

    def _show_thinking_indicator(self) -> None:
        self.query_one("#thinking", Static).display = True

    def _hide_thinking_indicator(self) -> None:
        self.query_one("#thinking", Static).display = False

    def on_key(self, event: Key) -> None:
        if event.key == "enter":
            event.stop()
            self._submit()
        # shift+enter inserts newline — TextArea handles this by default

    def _submit(self) -> None:
        area = self.query_one("#input-area", TextArea)
        text = area.text.strip()
        if not text:
            return
        area.clear()
        log = self.query_one("#scrollback", RichLog)
        log.write(f"[bold green]You:[/bold green] {text}")
        log.scroll_end(animate=False)
        if not self._input_future.done():
            self._input_future.set_result(text)

    async def emit(self, event: RunEvent) -> None:
        if isinstance(event, AssistantTextEvent):
            self._append_assistant_message(event.text)
        elif isinstance(event, ResultEvent):
            self._hide_thinking_indicator()

    async def prompt(self) -> str:
        """Block until the user submits a message via Enter."""
        self._hide_thinking_indicator()
        text = await self._input_future
        self._input_future = asyncio.get_event_loop().create_future()
        self._show_thinking_indicator()
        return text
