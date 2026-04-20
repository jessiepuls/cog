"""Main menu screen — lists workflows with live queue counts."""

import asyncio
from pathlib import Path

from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Footer, Header, Label, ListItem, ListView

from cog.core.tracker import IssueTracker
from cog.core.workflow import Workflow
from cog.ui.widgets.recent_runs import RecentRunsWidget
from cog.workflows import WORKFLOWS


class MainMenuScreen(Screen):
    BINDINGS = [("q", "quit", "Quit"), ("r", "refresh", "Refresh")]

    def __init__(self, project_dir: Path, tracker: IssueTracker) -> None:
        super().__init__()
        self._project_dir = project_dir
        self._tracker = tracker

    def compose(self) -> ComposeResult:
        yield Header()
        yield ListView(id="workflows")
        yield RecentRunsWidget(self._project_dir)
        yield Footer()

    async def on_mount(self) -> None:
        await self._populate_counts()

    async def on_screen_resume(self) -> None:
        await self._populate_counts()
        await self._refresh_recent_runs()

    async def action_refresh(self) -> None:
        await self._populate_counts()
        await self._refresh_recent_runs()

    async def _refresh_recent_runs(self) -> None:
        widget = self.query_one(RecentRunsWidget)
        await widget.refresh_runs()

    async def _populate_counts(self) -> None:
        list_view = self.query_one("#workflows", ListView)
        await list_view.clear()
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

    async def on_list_view_selected(self, event: ListView.Selected) -> None:
        list_view = self.query_one("#workflows", ListView)
        idx = list_view.index
        if idx is None or idx >= len(WORKFLOWS):
            return
        self.run_worker(self._launch_workflow(WORKFLOWS[idx]), exclusive=True)

    async def _launch_workflow(self, chosen_cls: type[Workflow]) -> None:
        from cog.ui.picker import PickerScreen, load_picker_history
        from cog.ui.preflight import PreflightScreen
        from cog.ui.wire import build_run_screen

        ok = await self.app.push_screen_wait(PreflightScreen(chosen_cls, self._project_dir))
        if not ok:
            return

        items = await self._tracker.list_by_label(chosen_cls.queue_label, assignee="@me")
        items.sort(key=lambda i: i.created_at)
        history = load_picker_history(self._project_dir)
        chosen_item = await self.app.push_screen_wait(
            PickerScreen(items, self._tracker, history=history)
        )
        if chosen_item is None:
            return

        run_screen = await build_run_screen(
            chosen_cls,
            self._project_dir,
            self.app,
            item_id=int(chosen_item.item_id),
        )
        await self.app.push_screen(run_screen)

    def action_quit(self) -> None:
        self.app.exit()

    async def _safe_count(self, label: str) -> int:
        items = await self._tracker.list_by_label(label, assignee="@me")
        return len(items)
