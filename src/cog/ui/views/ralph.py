"""RalphView — inline ralph flow (#121, #125).

Replaces the shell's Ralph stub. Drives the ralph workflow inline:

- Idle: list of agent-ready items (with history badges). Enter to start.
- Running: LogPaneWidget streams stage events; footer shows cost + elapsed.
- Post-run: completion or failure panel with per-stage cost breakdown.

Worker ownership stays on the view widget, so switching to another shell
view (Ctrl+1, Ctrl+2) doesn't cancel an in-flight ralph iteration — the
log pane stays mounted and events keep landing. Return via Ctrl+3 to see
the accurate in-progress state.
"""

from __future__ import annotations

import asyncio
import tempfile
import time
from pathlib import Path
from typing import Literal

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container
from textual.widget import Widget
from textual.widgets import Label, ListItem, ListView, Static
from textual.worker import Worker

from cog.core.context import ExecutionContext
from cog.core.errors import TrackerError
from cog.core.item import Item
from cog.core.tracker import IssueTracker
from cog.state import JsonFileStateCache
from cog.state_paths import project_state_dir
from cog.telemetry import TelemetryWriter
from cog.ui.messages import ViewAttention
from cog.ui.picker import PickerHistory, load_picker_history
from cog.ui.screens.run import StageCountingSink, stage_breakdown_line
from cog.ui.widgets.log_pane import LogPaneWidget
from cog.workflows.ralph import RalphWorkflow

_SubState = Literal["idle", "running", "post_run"]


class RalphView(Widget, can_focus=True):
    """Host of the ralph workflow's inline flow."""

    BINDINGS = [
        Binding("r", "refresh_queue", "Refresh", show=False),
        Binding("ctrl+c", "cancel_run", "Cancel"),
        Binding("enter", "dismiss_post_run", "Dismiss"),
    ]

    DEFAULT_CSS = """
    RalphView {
        layout: vertical;
        height: 1fr;
    }
    RalphView #ralph-status {
        height: 1;
        padding: 0 1;
        color: $text-muted;
    }
    RalphView #ralph-substate {
        height: 1fr;
    }
    RalphView #ralph-idle {
        height: 1fr;
        padding: 1;
    }
    RalphView #ralph-idle-title {
        text-style: bold;
        height: 1;
    }
    RalphView #ralph-idle-hint {
        color: $text-muted;
        height: 1;
        padding-bottom: 1;
    }
    RalphView #ralph-queue {
        border: solid $primary;
        height: 1fr;
    }
    RalphView #ralph-running {
        layout: vertical;
        height: 1fr;
    }
    RalphView #ralph-footer {
        height: 1;
        padding: 0 1;
        color: $text-muted;
    }
    RalphView #ralph-post-run {
        height: auto;
        padding: 1;
        border: solid $primary;
    }
    """

    def __init__(self, project_dir: Path, tracker: IssueTracker) -> None:
        super().__init__(id="view-ralph")
        self._project_dir = project_dir
        self._tracker = tracker
        self._substate: _SubState = "idle"
        self._items: list[Item] = []
        self._history: dict[str, PickerHistory] = {}
        self._active_item: Item | None = None
        self._worker: Worker[None] | None = None
        self._sink: StageCountingSink | None = None
        self._cumulative_cost = 0.0
        self._started_at = 0.0
        self._log_pane: LogPaneWidget | None = None

    def compose(self) -> ComposeResult:
        yield Static("", id="ralph-status")
        with Container(id="ralph-substate"):
            with Container(id="ralph-idle"):
                yield Static("Agent-ready queue", id="ralph-idle-title")
                yield Static(
                    "[dim]Enter on an item to start. `r` to refresh.[/dim]",
                    id="ralph-idle-hint",
                )
                yield ListView(id="ralph-queue")
            running = Container(id="ralph-running")
            running.display = False
            yield running
            post_run = Static("", id="ralph-post-run")
            post_run.display = False
            yield post_run

    async def on_mount(self) -> None:
        await self.refresh_queue()

    async def on_show(self) -> None:
        if self._substate == "idle":
            await self.refresh_queue()

    def busy_description(self) -> str | None:
        """Human-readable description of in-flight work, or None when idle.

        post_run is not busy — the run finished; the user is just reviewing.
        """
        if self._substate != "running":
            return None
        item_id = self._active_item.item_id if self._active_item else "?"
        return f"Ralph run on #{item_id}"

    def focus_content(self) -> None:
        """Called by the shell after this view becomes active. Focus the
        sub-widget or the view itself so keybinds fire without a click."""
        if self._substate == "idle":
            try:
                self.query_one("#ralph-queue", ListView).focus()
            except Exception:  # noqa: BLE001
                pass
        else:
            # running / post_run: bindings are on the view itself, so the
            # view needs focus for Enter / Ctrl+C to reach them.
            self.focus()

    async def action_refresh_queue(self) -> None:
        await self.refresh_queue()

    def action_cancel_run(self) -> None:
        if self._substate != "running":
            return
        if self._worker is not None:
            self._worker.cancel()

    def action_dismiss_post_run(self) -> None:
        if self._substate != "post_run":
            return
        self.run_worker(self._back_to_idle(), exclusive=True)

    async def _back_to_idle(self) -> None:
        self._switch_to("idle")
        await self.refresh_queue()

    async def refresh_queue(self) -> None:
        try:
            items = await self._tracker.list_by_label("agent-ready", assignee="@me")
        except TrackerError as e:
            self._set_status(f"[red]error listing queue: {e}[/red]")
            return
        except Exception as e:  # noqa: BLE001
            self._set_status(f"[red]error: {e}[/red]")
            return
        items.sort(key=lambda i: i.created_at)
        self._items = items
        self._history = load_picker_history(self._project_dir)

        list_view = self.query_one("#ralph-queue", ListView)
        await list_view.clear()
        if not items:
            await list_view.append(
                ListItem(Label("[dim]No items in queue.[/dim]"), id="queue-empty", disabled=True)
            )
            self._set_status("queue empty")
            return
        for i, item in enumerate(items):
            title = item.title if len(item.title) <= 80 else item.title[:79] + "…"
            badge = ""
            hist = self._history.get(item.item_id)
            if hist is not None:
                badge = (
                    f" [dim]\\[{hist.workflow} ×{hist.count}: "
                    f"last {hist.last_outcome}, ${hist.total_cost_usd:.2f}][/dim]"
                )
            await list_view.append(
                ListItem(Label(f"#{item.item_id} — {title}{badge}"), id=f"queue-{i}")
            )
        list_view.index = 0
        self._set_status(f"{len(items)} item(s) in queue")

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if self._substate != "idle":
            return
        chosen_id = event.item.id or ""
        if not chosen_id.startswith("queue-"):
            return
        try:
            idx = int(chosen_id.removeprefix("queue-"))
        except ValueError:
            return
        if idx >= len(self._items):
            return
        self._worker = self.run_worker(self._run_ralph(self._items[idx]), exclusive=True)

    async def _run_ralph(self, item: Item) -> None:
        self._active_item = item
        self._cumulative_cost = 0.0
        self._started_at = time.monotonic()
        self._switch_to("running")
        self._set_status(f"running ralph on #{item.item_id}")

        # Build the running sub-state: fresh LogPaneWidget + footer.
        running = self.query_one("#ralph-running", Container)
        await running.remove_children()
        log = LogPaneWidget()
        self._log_pane = log
        await running.mount(log)
        footer = Static(self._footer_text(), id="ralph-footer")
        await running.mount(footer)
        self._clock_interval = self.set_interval(1.0, self._refresh_footer)

        state_dir = project_state_dir(self._project_dir)
        cache = JsonFileStateCache(state_dir / "state.json")
        cache.load()
        telemetry = TelemetryWriter(state_dir)
        tmp_dir = Path(tempfile.mkdtemp(prefix="cog-"))

        self._sink = StageCountingSink(log, on_cost=self._add_cost)
        ctx = ExecutionContext(
            project_dir=self._project_dir,
            tmp_dir=tmp_dir,
            state_cache=cache,
            headless=False,
            item=item,
            telemetry=telemetry,
            event_sink=self._sink,
        )

        from cog.hosts.github import GitHubGitHost
        from cog.runners.claude_cli import ClaudeCliRunner
        from cog.runners.docker_sandbox import DockerSandbox

        sandbox = DockerSandbox()
        runner = ClaudeCliRunner(sandbox)
        host = GitHubGitHost(self._project_dir)
        workflow = RalphWorkflow(runner=runner, tracker=self._tracker, host=host)

        from cog.core.workflow import StageExecutor

        header: str
        try:
            results = await StageExecutor().run(workflow, ctx)
            if not results:
                header = "[yellow]No eligible items — queue drained or deferred.[/yellow]"
            else:
                header = (
                    f"[green]✓ Complete[/green] — ${self._cumulative_cost:.3f} · "
                    f"{self._elapsed()} total"
                )
        except asyncio.CancelledError:
            if self._sink is not None:
                self._sink.mark_running_stages_failed()
            header = "[yellow]Cancelled[/yellow]"
            self._render_post_run(header)
            self._switch_to("post_run")
            raise
        except Exception as e:  # noqa: BLE001 — surface any unexpected failure
            if self._sink is not None:
                self._sink.mark_running_stages_failed()
            header = f"[red]✗ Failed:[/red] {e!s}"
        finally:
            self._clock_interval.stop()

        self._render_post_run(header)
        self._switch_to("post_run")
        self._set_status(f"run finished on #{item.item_id}")

    def _render_post_run(self, header: str) -> None:
        stages = self._sink.stages if self._sink is not None else []
        breakdown = stage_breakdown_line(stages)
        body = f"{header}\n{breakdown}" if breakdown else header
        body += "\n\n[dim]Press Enter to return to the queue.[/dim]"
        self.query_one("#ralph-post-run", Static).update(body)

    def _add_cost(self, cost: float) -> None:
        self._cumulative_cost += cost
        self._refresh_footer()

    def _elapsed(self) -> str:
        if self._started_at == 0.0:
            return "0s"
        secs = int(time.monotonic() - self._started_at)
        if secs < 60:
            return f"{secs}s"
        return f"{secs // 60}m{secs % 60:02d}s"

    def _footer_text(self) -> str:
        return f"cost=${self._cumulative_cost:.3f}  elapsed={self._elapsed()}"

    def _refresh_footer(self) -> None:
        if self._substate != "running":
            return
        try:
            footer = self.query_one("#ralph-footer", Static)
        except Exception:  # noqa: BLE001 — footer not yet mounted
            return
        footer.update(self._footer_text())

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if action == "cancel_run":
            return True if self._substate == "running" else None
        if action == "dismiss_post_run":
            return True if self._substate == "post_run" else None
        if action == "refresh_queue":
            return True if self._substate == "idle" else None
        return True

    def _switch_to(self, substate: _SubState) -> None:
        self._substate = substate
        self.query_one("#ralph-idle", Container).display = substate == "idle"
        self.query_one("#ralph-running", Container).display = substate == "running"
        self.query_one("#ralph-post-run", Static).display = substate == "post_run"
        self.refresh_bindings()
        # Re-focus the right sub-widget for the new substate — the shell's
        # call_after_refresh hook only fires on view switch, not on an
        # internal substate change (e.g. running → post_run when a run
        # completes).
        self.call_after_refresh(self.focus_content)
        if substate == "post_run":
            self.post_message(ViewAttention("ralph", reason="run complete"))

    def _set_status(self, text: str) -> None:
        self.query_one("#ralph-status", Static).update(text)
