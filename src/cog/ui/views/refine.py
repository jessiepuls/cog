"""RefineView — inline refine flow (#121, #124).

Replaces the shell's Refine stub. Drives the refine workflow inline:

- Idle: list of needs-refinement items; Enter to start.
- Running: split pane — issue body (left) + chat (right).
- Review: split pane — issue body (left) + proposed body (right).

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
from textual import events
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
from cog.ui.messages import QueueCountsStale, ViewAttention
from cog.ui.picker import _format_assignees
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
        # Splitter bindings — active in running and review
        Binding("ctrl+comma", "narrow_issue", "Narrow issue"),
        Binding("ctrl+full_stop", "widen_issue", "Widen issue"),
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
    RefineView #refine-active {
        layout: vertical;
        height: 1fr;
    }
    RefineView #review-title-strip {
        height: 3;
        padding: 1;
        background: $surface;
        border-bottom: solid $primary;
    }
    RefineView #refine-panes {
        layout: horizontal;
        height: 1fr;
    }
    RefineView #refine-panes.vertical {
        layout: vertical;
    }
    RefineView .refine-pane {
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
        self._split_pct: int = 50

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
            # Active state — used for both running and review sub-states
            with Container(id="refine-active") as active:
                active.display = False
                yield Static("", id="review-title-strip")
                with Horizontal(id="refine-panes"):
                    with ScrollableContainer(classes="refine-pane", id="refine-original"):
                        yield Static("", id="refine-original-body")
                    yield Container(classes="refine-pane", id="refine-right")

    async def on_mount(self) -> None:
        self.query_one("#review-title-strip", Static).display = False
        await self.refresh_queue()

    async def on_show(self) -> None:
        if self._substate == "idle":
            await self.refresh_queue()

    def on_resize(self, event: events.Resize) -> None:
        try:
            panes = self.query_one("#refine-panes", Horizontal)
        except Exception:  # noqa: BLE001
            return
        if self.size.width < 100:
            panes.add_class("vertical")
        else:
            panes.remove_class("vertical")
        self._apply_split()

    def needs_attention(self) -> str | None:
        if self._substate == "review":
            return "review ready"
        if self._substate == "running" and self._chat_pane is not None:
            future = self._chat_pane._input_future
            if future is not None and not future.done():
                return "awaiting reply"
        return None

    def busy_description(self) -> str | None:
        if self._substate == "idle":
            return None
        item_id = self._active_item.item_id if self._active_item else "?"
        if self._substate == "running":
            return f"Refine interview on #{item_id}"
        if self._substate == "review":
            return f"Refine review pending on #{item_id}"
        return None

    def focus_content(self) -> None:
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
            self.focus()

    async def action_refresh_queue(self) -> None:
        await self.refresh_queue()

    async def refresh_queue(self) -> None:
        try:
            items = await self._tracker.list_by_label("needs-refinement")
        except Exception as e:  # noqa: BLE001
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
            assignees_suffix = _format_assignees(item.assignees)
            await list_view.append(
                ListItem(Label(f"#{item.item_id} — {title}{assignees_suffix}"), id=f"queue-{i}")
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
        item = self._items[idx]
        self.run_worker(self._run_refine(item), exclusive=True)

    async def _run_refine(self, item: Item) -> None:
        self._active_item = item
        self._split_pct = 50
        self._switch_to("running")
        self._set_status(self._format_status("Refining", item=item))

        # Mount chat into right pane. Recreate for each run so scrollback resets.
        right = self.query_one("#refine-right", Container)
        await right.remove_children()
        chat = ChatPaneWidget()
        self._chat_pane = chat
        await right.mount(chat)
        self._render_left_pane(item)
        self._apply_split()

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
        from cog.runners.claude_cli import ClaudeCliRunner
        from cog.runners.docker_sandbox import DockerSandbox

        sandbox = DockerSandbox()
        runner = ClaudeCliRunner(sandbox)
        workflow = RefineWorkflow(runner=runner, tracker=self._tracker)

        from cog.core.workflow import StageExecutor

        try:
            await StageExecutor().run(workflow, ctx)
            self._set_status(self._format_status("Completed", item=item))
            self.post_message(QueueCountsStale())
        except Exception as e:  # noqa: BLE001
            self._set_status(
                f"[red]{self._format_status('Failed', item=item, suffix=f': {e}')}[/red]"
            )

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

        # Hide the chat pane (don't detach it — Textual rebuilds children
        # on remount, which would clear RichLog scrollback) and mount the
        # proposed body alongside it.
        right = self.query_one("#refine-right", Container)
        if self._chat_pane is not None:
            self._chat_pane.display = False
        proposed_scroll = ScrollableContainer(id="review-proposed-scroll")
        await right.mount(proposed_scroll)
        proposed = Static("", id="review-proposed-body")
        await proposed_scroll.mount(proposed)
        proposed.update(Markdown(str(proposed_body) or "*(empty)*"))

        self._render_title_strip(original_title, proposed_title)
        self._switch_to("review")
        if self._active_item is not None:
            self._set_status(self._format_status("Reviewing", item=self._active_item))

        self._review_future = asyncio.get_running_loop().create_future()
        try:
            outcome = await self._review_future
        finally:
            self._review_future = None
            # Restore: remove proposed scroll wrapper, unhide chat pane (instance preserved).
            try:
                await self.query_one("#review-proposed-scroll", ScrollableContainer).remove()
            except Exception:  # noqa: BLE001
                pass
            if self._chat_pane is not None:
                self._chat_pane.display = True
            self._switch_to("running")
            if self._active_item is not None:
                self._set_status(self._format_status("Refining", item=self._active_item))

        return outcome

    def _render_title_strip(self, original_title: str, proposed_title: str) -> None:
        strip = self.query_one("#review-title-strip", Static)
        if original_title == proposed_title:
            strip.update(f"Title: {original_title} [dim][unchanged][/dim]")
        else:
            strip.update(f"Title: {original_title} → [bold]{proposed_title}[/bold]")

    def _render_left_pane(self, item: Item) -> None:
        parts = [item.body or "*(empty body)*"]
        for comment in item.comments:
            ts = comment.created_at.strftime("%Y-%m-%d %H:%M")
            parts.append(f"\n---\n\n**@{comment.author}** · {ts}\n\n{comment.body}")
        content = "".join(parts)
        self.query_one("#refine-original-body", Static).update(Markdown(content))

    def _format_status(self, verb: str, *, item: Item, suffix: str = "") -> str:
        labels = f" [{', '.join(item.labels)}]" if item.labels else ""
        return f"{verb} #{item.item_id} - {item.title}{labels}{suffix}"

    def _apply_split(self) -> None:
        try:
            panes = self.query_one("#refine-panes", Horizontal)
            orig = self.query_one("#refine-original", ScrollableContainer)
            if panes.has_class("vertical"):
                orig.styles.height = f"{self._split_pct}%"
                orig.styles.width = "1fr"
            else:
                orig.styles.width = f"{self._split_pct}%"
                orig.styles.height = "1fr"
        except Exception:  # noqa: BLE001
            pass

    def action_narrow_issue(self) -> None:
        self._split_pct = max(20, self._split_pct - 5)
        self._apply_split()

    def action_widen_issue(self) -> None:
        self._split_pct = min(80, self._split_pct + 5)
        self._apply_split()

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
            proposed_widget = self.query_one("#review-proposed-body", Static)
            proposed_widget.update(Markdown(edited or "*(empty)*"))
            self._render_title_strip(
                str(self._review_state.get("original_title", "")),
                str(self._review_state.get("proposed_title", "")),
            )

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
        if action in ("review_accept", "review_edit", "review_abandon"):
            return True if self._substate == "review" else None
        if action == "refresh_queue":
            return True if self._substate == "idle" else None
        if action in ("narrow_issue", "widen_issue"):
            return True if self._substate in ("running", "review") else None
        return True

    # ---- sub-state helpers ------------------------------------------------

    def _switch_to(self, substate: _SubState) -> None:
        self._substate = substate
        self.query_one("#refine-idle", Container).display = substate == "idle"
        self.query_one("#refine-active", Container).display = substate != "idle"
        self.query_one("#review-title-strip", Static).display = substate == "review"
        self.refresh_bindings()
        self.call_after_refresh(self.focus_content)
        if substate == "review":
            self.post_message(ViewAttention("refine", reason="review ready"))

    def _set_status(self, text: str) -> None:
        self.query_one("#refine-status", Static).update(text)
