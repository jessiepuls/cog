"""Multi-turn chat widget for interactive workflows (e.g. refine)."""

import asyncio

from rich.console import Group
from rich.markdown import Markdown
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.widget import Widget
from textual.widgets import RichLog, Static, TextArea

from cog.core.runner import (
    AssistantTextEvent,
    ResultEvent,
    RunEvent,
    StatusEvent,
    ToolUseEvent,
)
from cog.ui.widgets._shared import tool_preview


class ChatPaneWidget(Widget):
    """Multi-turn chat with Claude. Implements RunEventSink + UserInputProvider."""

    # Priority bindings run BEFORE the focused TextArea processes the key, so
    # Enter submits instead of being consumed by TextArea as a newline insert.
    # shift+enter is not bound here, so it falls through to TextArea's default
    # (insert newline).
    BINDINGS = [
        Binding("enter", "submit", "Submit", priority=True),
        Binding("escape", "end_interview", "End interview", priority=True),
        Binding("ctrl+d", "end_interview", "End interview", priority=True, show=False),
    ]

    DEFAULT_CSS = """
    ChatPaneWidget {
        height: 1fr;
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
        self._input_future: asyncio.Future[str | None] | None = None

    def compose(self) -> ComposeResult:
        yield RichLog(id="scrollback", highlight=True, markup=True, wrap=True)
        yield Static("⏳ Thinking…", id="thinking")
        yield TextArea(id="input-area")

    def _ensure_future(self) -> asyncio.Future[str | None]:
        if self._input_future is None or self._input_future.done():
            self._input_future = asyncio.get_running_loop().create_future()
        return self._input_future

    def on_mount(self) -> None:
        self.query_one("#thinking", Static).display = False
        self._ensure_future()

    def _append_message(self, renderable: object) -> None:
        log = self.query_one("#scrollback", RichLog)
        log.write("")
        log.write(renderable)
        log.scroll_end(animate=False)

    def _append_tool_line(self, markup: str) -> None:
        log = self.query_one("#scrollback", RichLog)
        log.write(markup)
        log.scroll_end(animate=False)

    def _show_thinking_indicator(self) -> None:
        self.query_one("#thinking", Static).display = True

    def _hide_thinking_indicator(self) -> None:
        self.query_one("#thinking", Static).display = False

    def action_submit(self) -> None:
        self._submit()

    def action_end_interview(self) -> None:
        self._end_interview()

    def _submit(self) -> None:
        area = self.query_one("#input-area", TextArea)
        text = area.text.strip()
        area.clear()
        if text:
            self._append_message(
                Group(
                    Text.from_markup("[bold green]You:[/bold green]"),
                    Text(text),
                )
            )
        future = self._ensure_future()
        if not future.done():
            future.set_result(text)

    def _end_interview(self) -> None:
        future = self._ensure_future()
        if not future.done():
            future.set_result(None)

    async def emit(self, event: RunEvent) -> None:
        if isinstance(event, AssistantTextEvent):
            self._append_message(
                Group(
                    Text.from_markup("[bold blue]Claude:[/bold blue]"),
                    Markdown(event.text),
                )
            )
        elif isinstance(event, ToolUseEvent):
            preview = tool_preview(event)
            suffix = f": {preview}" if preview else ""
            self._append_tool_line(f"[dim]🔧 {event.tool}{suffix}[/dim]")
        elif isinstance(event, ResultEvent):
            self._hide_thinking_indicator()
        elif isinstance(event, StatusEvent):
            self._append_tool_line(f"[dim]{event.message}[/dim]")

    async def prompt(self) -> str | None:
        """Block until the user submits a message via Enter (str, possibly empty),
        or ends the interview via Escape / Ctrl+D (None)."""
        self._hide_thinking_indicator()
        future = self._ensure_future()
        result = await future
        self._input_future = None
        if result is not None:
            self._show_thinking_indicator()
        return result
