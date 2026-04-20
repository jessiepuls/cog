"""RefineView — inline refine flow (#121, #124).

Replaces the shell's Refine stub. Drives the refine workflow inline:

- Idle: list of needs-refinement items; Enter to start.
- Running: ChatPaneWidget streams the interview; user replies inline.
- Review: original vs. proposed body panes; `a` accept, `e` edit, `q` abandon.

Worker ownership stays on this widget, so switching to another shell
view (Ctrl+1, Ctrl+3) doesn't cancel the interview — the chat pane
scrollback and input state are preserved.
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from typing import Literal

from rich.markdown import Markdown
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, ScrollableContainer
from textual.widget import Widget
from textual.widgets import Label, ListItem, ListView, Static

from cog.core.context import ExecutionContext
from cog.core.item import Item
from cog.core.tracker import IssueTracker
from cog.state import JsonFileStateCache
from cog.state_paths import project_state_dir
from cog.telemetry import TelemetryWriter
from cog.ui.editor import suspend_and_edit
from cog.ui.messages import ViewAttention
from cog.ui.widgets.chat_pane import ChatPaneWidget
from cog.workflows.refine import RefineWorkflow, ReviewDecision, ReviewOutcome

_SubState = Literal["idle", "running", "review"]


class _AttentionInputProvider:
    """Wraps ChatPaneWidget's prompt() to post a ViewAttention when the
    interview loop blocks waiting for user input — so the sidebar shows a
    dot on the Refine row if the user is on another view."""

    def __init__(self, inner: ChatPaneWidget, view: RefineView) -> None:
        self._inner = inner
        self._view = view

    async def prompt(self) -> str | None:
        self._view.post_message(ViewAttention("refine", reason="awaiting reply"))
        return await self._inner.prompt()


class RefineView(Widget, can_focus=True):
    """Host of the refine workflow's inline flow."""

    BINDINGS = [
        Binding("r", "refresh_queue", "Refresh", show=False),
        # Review-state bindings — `check_action` hides them when we're
        # not on the review sub-state.
        Binding("a", "review_accept", "Accept"),
        Binding("e", "review_edit", "Edit"),
        Binding("shift+q", "review_abandon", "Abandon"),
    ]

    DEFAULT_CSS = """
    RefineView {
        layout: vertical;
        height: 1fr;
    }
    RefineView #refine-status {
        height: 1;
        padding: 0 1;
        color: $text-muted;
    }
    RefineView #refine-substate {
        height: 1fr;
    }
    RefineView #refine-idle {
        height: 1fr;
        padding: 1;
    }
    RefineView #refine-idle-title {
        text-style: bold;
        height: 1;
    }
    RefineView #refine-idle-hint {
        color: $text-muted;
        height: 1;
        padding-bottom: 1;
    }
    RefineView #refine-queue {
        border: solid $primary;
        height: 1fr;
    }
    RefineView #refine-running {
        height: 1fr;
    }
    RefineView #refine-review {
        layout: vertical;
        height: 1fr;
    }
    RefineView #review-title-strip {
        height: 3;
        padding: 1;
        background: $surface;
        border-bottom: solid $primary;
    }
    RefineView #review-panes {
        layout: horizontal;
        height: 1fr;
    }
    RefineView .review-pane {
        width: 1fr;
        height: 1fr;
        border: solid $primary;
        padding: 1;
    }
    """

    def __init__(self, project_dir: Path, tracker: IssueTracker) -> None:
        super().__init__(id="view-refine")
        self._project_dir = project_dir
        self._tracker = tracker
        self._substate: _SubState = "idle"
        self._items: list[Item] = []
        self._active_item: Item | None = None
        self._review_future: asyncio.Future[ReviewOutcome] | None = None
        self._review_state: dict[str, str | Path] = {}
        self._chat_pane: ChatPaneWidget | None = None

    def compose(self) -> ComposeResult:
        yield Static("", id="refine-status")
        with Container(id="refine-substate"):
            with Container(id="refine-idle"):
                yield Static("Needs refinement", id="refine-idle-title")
                yield Static(
                    "[dim]Enter on an item to start. `r` to refresh.[/dim]",
                    id="refine-idle-hint",
                )
                yield ListView(id="refine-queue")
            # Running state — holds the chat pane
            running = Container(id="refine-running")
            running.display = False
            yield running
            # Review state — two panes with original vs proposed body
            with Container(id="refine-review") as review:
                review.display = False
                yield Static("", id="review-title-strip")
                with Horizontal(id="review-panes"):
                    with ScrollableContainer(classes="review-pane", id="review-original"):
                        yield Static("", id="review-original-body")
                    with ScrollableContainer(classes="review-pane", id="review-proposed"):
                        yield Static("", id="review-proposed-body")

    async def on_mount(self) -> None:
        await self.refresh_queue()

    async def on_show(self) -> None:
        # Called when the view becomes visible in the shell. Refresh the
        # queue if we're idle so counts stay current across view switches.
        if self._substate == "idle":
            await self.refresh_queue()

    def busy_description(self) -> str | None:
        """Human-readable description of in-flight work, or None when idle."""
        if self._substate == "idle":
            return None
        item_id = self._active_item.item_id if self._active_item else "?"
        if self._substate == "running":
            return f"Refine interview on #{item_id}"
        if self._substate == "review":
            return f"Refine review pending on #{item_id}"
        return None

    def focus_content(self) -> None:
        """Called by the shell after this view becomes active and by
        _switch_to on internal substate changes. Focus the sub-widget or
        the view itself so keybinds fire without a click."""
        if self._substate == "idle":
            try:
                self.query_one("#refine-queue", ListView).focus()
            except Exception:  # noqa: BLE001
                pass
        elif self._substate == "running" and self._chat_pane is not None:
            try:
                from textual.widgets import TextArea

                self._chat_pane.query_one("#input-area", TextArea).focus()
            except Exception:  # noqa: BLE001
                pass
        elif self._substate == "review":
            # Review bindings (a / e / shift+q) fire on the view itself —
            # focus the view so Enter-style keypresses reach them.
            self.focus()

    async def action_refresh_queue(self) -> None:
        await self.refresh_queue()

    async def refresh_queue(self) -> None:
        try:
            items = await self._tracker.list_by_label("needs-refinement", assignee="@me")
        except Exception as e:  # noqa: BLE001 — surface any list-failure
            self._set_status(f"[red]error listing queue: {e}[/red]")
            return
        items.sort(key=lambda i: i.created_at)
        self._items = items
        list_view = self.query_one("#refine-queue", ListView)
        await list_view.clear()
        if not items:
            await list_view.append(
                ListItem(Label("[dim]No items in queue.[/dim]"), id="queue-empty", disabled=True)
            )
            self._set_status("queue empty")
            return
        for i, item in enumerate(items):
            title = item.title if len(item.title) <= 80 else item.title[:79] + "…"
            await list_view.append(ListItem(Label(f"#{item.item_id} — {title}"), id=f"queue-{i}"))
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
        item = self._items[idx]
        self.run_worker(self._run_refine(item), exclusive=True)

    async def _run_refine(self, item: Item) -> None:
        self._active_item = item
        self._switch_to("running")
        self._set_status(f"running refine on #{item.item_id}")

        # Lazily (re)create the chat pane for each iteration so scrollback
        # resets between runs. Mount into #refine-running.
        running = self.query_one("#refine-running", Container)
        await running.remove_children()
        chat = ChatPaneWidget()
        self._chat_pane = chat
        await running.mount(chat)

        # Build per-iteration ctx. Everything scoped to this run.
        state_dir = project_state_dir(self._project_dir)
        cache = JsonFileStateCache(state_dir / "state.json")
        cache.load()
        telemetry = TelemetryWriter(state_dir)
        tmp_dir = Path(tempfile.mkdtemp(prefix="cog-"))
        ctx = ExecutionContext(
            project_dir=self._project_dir,
            tmp_dir=tmp_dir,
            state_cache=cache,
            headless=False,
            item=item,
            telemetry=telemetry,
            event_sink=chat,
            input_provider=_AttentionInputProvider(chat, self),
            review_provider=self,
        )
        # Workflow needs a runner + tracker. Build them here since the view
        # is the entry point for shell-driven refine iterations.
        from cog.runners.claude_cli import ClaudeCliRunner
        from cog.runners.docker_sandbox import DockerSandbox

        sandbox = DockerSandbox()
        runner = ClaudeCliRunner(sandbox)
        workflow = RefineWorkflow(runner=runner, tracker=self._tracker)

        from cog.core.workflow import StageExecutor

        try:
            await StageExecutor().run(workflow, ctx)
            self._set_status(f"completed refine on #{item.item_id}")
        except Exception as e:  # noqa: BLE001 — error path surfaces to status
            self._set_status(f"[red]refine failed: {e}[/red]")

        self._active_item = None
        self._review_future = None
        self._review_state = {}
        self._switch_to("idle")
        await self.refresh_queue()

    # ---- ReviewProvider protocol -----------------------------------------

    async def review(
        self,
        *,
        original_title: str,
        original_body: str,
        proposed_title: str,
        proposed_body: str,
        tmp_dir: Path,
    ) -> ReviewOutcome:
        self._review_state = {
            "original_title": original_title,
            "original_body": original_body,
            "proposed_title": proposed_title,
            "proposed_body": proposed_body,
            "tmp_dir": tmp_dir,
        }
        self._render_review_panes()
        self._switch_to("review")
        self.refresh_bindings()

        self._review_future = asyncio.get_running_loop().create_future()
        try:
            outcome = await self._review_future
        finally:
            self._review_future = None
        return outcome

    def _render_review_panes(self) -> None:
        title_strip = self.query_one("#review-title-strip", Static)
        ot = self._review_state.get("original_title", "")
        pt = self._review_state.get("proposed_title", "")
        if ot == pt:
            title_strip.update(f"Title: {ot} [dim][unchanged][/dim]")
        else:
            title_strip.update(f"Title: {ot} → [bold]{pt}[/bold]")
        orig = self.query_one("#review-original-body", Static)
        prop = self.query_one("#review-proposed-body", Static)
        orig.update(Markdown(str(self._review_state.get("original_body", "") or "*(empty)*")))
        prop.update(Markdown(str(self._review_state.get("proposed_body", "") or "*(empty)*")))

    def action_review_accept(self) -> None:
        self._resolve_review(ReviewDecision.ACCEPT)

    def action_review_abandon(self) -> None:
        self._resolve_review(ReviewDecision.ABANDON)

    async def action_review_edit(self) -> None:
        if self._substate != "review":
            return
        proposed = str(self._review_state.get("proposed_body", ""))
        tmp_dir = self._review_state.get("tmp_dir", Path(tempfile.gettempdir()))
        assert isinstance(tmp_dir, Path)
        edited = await suspend_and_edit(self.app, proposed, tmp_dir)
        if edited is not None:
            self._review_state["proposed_body"] = edited
            self._render_review_panes()

    def _resolve_review(self, decision: ReviewDecision) -> None:
        if self._substate != "review":
            return
        if self._review_future is None or self._review_future.done():
            return
        self._review_future.set_result(
            ReviewOutcome(
                decision=decision,
                final_body=str(self._review_state.get("proposed_body", "")),
                final_title=str(self._review_state.get("proposed_title", "")),
            )
        )

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        # Hide review bindings from the footer outside the review sub-state.
        if action in ("review_accept", "review_edit", "review_abandon"):
            return True if self._substate == "review" else None
        if action == "refresh_queue":
            return True if self._substate == "idle" else None
        return True

    # ---- sub-state helpers ------------------------------------------------

    def _switch_to(self, substate: _SubState) -> None:
        self._substate = substate
        self.query_one("#refine-idle", Container).display = substate == "idle"
        self.query_one("#refine-running", Container).display = substate == "running"
        self.query_one("#refine-review", Container).display = substate == "review"
        self.refresh_bindings()
        # Internal substate changes (e.g. running → review) don't trigger
        # the shell's auto-focus hook, so re-focus here.
        self.call_after_refresh(self.focus_content)
        if substate == "review":
            self.post_message(ViewAttention("refine", reason="review ready"))

    def _set_status(self, text: str) -> None:
        self.query_one("#refine-status", Static).update(text)
