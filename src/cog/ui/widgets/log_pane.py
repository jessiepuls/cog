"""Append-only scrolling log for autonomous workflow stages."""

from rich.console import RenderableType
from rich.markdown import Markdown
from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import RichLog

from cog.core.runner import AssistantTextEvent, ResultEvent, RunEvent, StatusEvent, ToolUseEvent
from cog.ui.widgets._shared import tool_preview


class LogPaneWidget(Widget):
    """Append-only scrolling log; auto-scrolls unless user has scrolled up."""

    DEFAULT_CSS = """
    LogPaneWidget {
        height: 1fr;
        border: solid $accent;
    }
    LogPaneWidget RichLog {
        height: 1fr;
    }
    """

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        self._pinned = True  # stick to bottom

    def compose(self) -> ComposeResult:
        yield RichLog(id="log", highlight=True, markup=True, auto_scroll=False, wrap=True)

    def _log(self) -> RichLog:
        return self.query_one("#log", RichLog)

    def _append_line(self, content: str | RenderableType) -> None:
        log = self._log()
        log.write(content)
        if self._pinned:
            log.scroll_end(animate=False)

    def on_scroll(self) -> None:
        log = self._log()
        self._pinned = log.scroll_y >= log.max_scroll_y

    async def emit(self, event: RunEvent) -> None:
        if isinstance(event, AssistantTextEvent):
            self._append_line(Markdown(event.text))
        elif isinstance(event, ToolUseEvent):
            preview = tool_preview(event)
            suffix = f": {preview}" if preview else ""
            self._append_line(f"🔧 {event.tool}{suffix}")
        elif isinstance(event, ResultEvent):
            cost = event.result.total_cost_usd
            self._append_line(f"[dim]─── stage complete: ${cost:.3f} ───[/dim]")
        elif isinstance(event, StatusEvent):
            self._append_line(f"[dim]{event.message}[/dim]")
