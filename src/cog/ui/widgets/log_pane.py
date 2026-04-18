"""Append-only scrolling log for autonomous workflow stages."""

from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import RichLog

from cog.core.runner import AssistantTextEvent, ResultEvent, RunEvent, ToolUseEvent


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
        yield RichLog(id="log", highlight=True, markup=True, auto_scroll=False)

    def _log(self) -> RichLog:
        return self.query_one("#log", RichLog)

    def _append_line(self, text: str) -> None:
        log = self._log()
        log.write(text)
        if self._pinned:
            log.scroll_end(animate=False)

    def on_scroll(self) -> None:
        log = self._log()
        self._pinned = log.scroll_y >= log.max_scroll_y

    async def emit(self, event: RunEvent) -> None:
        if isinstance(event, AssistantTextEvent):
            self._append_line(event.text)
        elif isinstance(event, ToolUseEvent):
            preview = event.input.get("command") or event.input.get("file_path") or ""
            self._append_line(f"🔧 {event.tool}: {preview}")
        elif isinstance(event, ResultEvent):
            cost = event.result.total_cost_usd
            self._append_line(f"[dim]─── stage complete: ${cost:.3f} ───[/dim]")
