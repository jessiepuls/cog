"""Main menu screen — lists workflows with live queue counts."""

import asyncio
from collections.abc import Callable
from pathlib import Path

from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Footer, Header, Label, ListItem, ListView

from cog.core.tracker import IssueTracker
from cog.core.workflow import Workflow
from cog.workflows import WORKFLOWS


class MainMenuScreen(Screen):
    BINDINGS = [("q", "quit", "Quit")]

    def __init__(
        self,
        project_dir: Path,
        tracker: IssueTracker,
        run_screen_factory: Callable[[type[Workflow]], Screen] | None = None,
    ) -> None:
        super().__init__()
        self._project_dir = project_dir
        self._tracker = tracker
        self._run_screen_factory = run_screen_factory

    def compose(self) -> ComposeResult:
        yield Header()
        yield ListView(id="workflows")
        yield Footer()

    async def on_mount(self) -> None:
        list_view = self.query_one("#workflows", ListView)
        results = await asyncio.gather(
            *(self._safe_count(w.queue_label) for w in WORKFLOWS),
            return_exceptions=True,
        )
        for cls, count in zip(WORKFLOWS, results, strict=False):
            if isinstance(count, BaseException):
                label = f"{cls.name}: ? {cls.queue_label} (error)"
            else:
                label = f"{cls.name}: {count} {cls.queue_label}"
            await list_view.append(ListItem(Label(label)))
        if WORKFLOWS:
            list_view.index = 0

    async def _safe_count(self, label: str) -> int:
        items = await self._tracker.list_by_label(label, assignee="@me")
        return len(items)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        list_view = self.query_one("#workflows", ListView)
        idx = list_view.index
        if idx is None or idx >= len(WORKFLOWS):
            return
        chosen_cls = WORKFLOWS[idx]
        if self._run_screen_factory is not None:
            self.app.push_screen(self._run_screen_factory(chosen_cls))

    def action_quit(self) -> None:
        self.app.exit()
