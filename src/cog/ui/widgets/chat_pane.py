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
        self._input_future: asyncio.Future[str] | None = None

    def compose(self) -> ComposeResult:
        yield RichLog(id="scrollback", highlight=True, markup=True)
        yield Static("⏳ Thinking…", id="thinking")
        yield TextArea(id="input-area")

    def _ensure_future(self) -> asyncio.Future[str]:
        if self._input_future is None or self._input_future.done():
            self._input_future = asyncio.get_running_loop().create_future()
        return self._input_future

    def on_mount(self) -> None:
        self.query_one("#thinking", Static).display = False
        self._ensure_future()

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
        future = self._ensure_future()
        if not future.done():
            future.set_result(text)

    async def emit(self, event: RunEvent) -> None:
        if isinstance(event, AssistantTextEvent):
            self._append_assistant_message(event.text)
        elif isinstance(event, ResultEvent):
            self._hide_thinking_indicator()

    async def prompt(self) -> str:
        """Block until the user submits a message via Enter."""
        self._hide_thinking_indicator()
        future = self._ensure_future()
        text = await future
        self._input_future = None
        self._show_thinking_indicator()
        return text
