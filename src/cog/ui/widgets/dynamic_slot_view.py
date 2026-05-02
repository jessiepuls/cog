"""DynamicSlotView — per-slot view widget for parallel workflow runs (#192).

One instance per active dynamic slot. Hosts the live transcript pane
during ``running``, and an in-slot dismiss/review panel during
``awaiting_dismiss``.

Lifecycle:
  Implement: running (log pane) → awaiting_dismiss (post-run panel)
             → user hits Enter → SlotDismissed posted → slot closed.
  Refine:    running (chat + issue pane) → review (awaiting_dismiss;
             proposed body shown) → user accepts/abandons → SlotDismissed.
"""

from __future__ import annotations

import asyncio
import tempfile
import time
from pathlib import Path
from typing import Literal

from rich.markdown import Markdown
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, ScrollableContainer
from textual.widget import Widget
from textual.widgets import Static
from textual.worker import Worker

from cog.core.context import ExecutionContext
from cog.core.item import Item
from cog.core.tracker import IssueTracker
from cog.state import JsonFileStateCache
from cog.state_paths import project_state_dir
from cog.telemetry import TelemetryWriter
from cog.ui.dynamic_slots import DynamicSlot, DynamicSlotRegistry
from cog.ui.messages import SlotDismissed, SlotStateChanged
from cog.ui.screens.run import StageCountingSink, stage_breakdown_line
from cog.ui.widgets.chat_pane import ChatPaneWidget
from cog.ui.widgets.log_pane import LogPaneWidget

_SubState = Literal["running", "reviewing", "post_run"]


class _SlotAttentionInputProvider:
    """Wraps ChatPaneWidget.prompt() to mark the slot as needing attention."""

    def __init__(self, inner: ChatPaneWidget, view: DynamicSlotView) -> None:
        self._inner = inner
        self._view = view

    async def prompt(self) -> str | None:
        self._view._post_state_changed()
        return await self._inner.prompt()


class _AbortConfirmScreen:
    pass  # imported lazily to avoid circular deps at module level


class DynamicSlotView(Widget, can_focus=True):
    """Host widget for one dynamic workflow slot.

    Mounted into #content-area alongside static views; toggled by display
    exactly like them.
    """

    BINDINGS = [
        Binding("x", "abort", "Abort", show=True),
        Binding("enter", "dismiss", "Dismiss", show=False),
        Binding("a", "review_accept", "Accept", show=False),
        Binding("e", "review_edit", "Edit", show=False),
        Binding("shift+q", "review_abandon", "Abandon", show=False),
        Binding("ctrl+comma", "narrow_pane", "Narrow", show=False),
        Binding("ctrl+full_stop", "widen_pane", "Widen", show=False),
    ]

    DEFAULT_CSS = """
    DynamicSlotView {
        layout: vertical;
        height: 1fr;
    }
    DynamicSlotView #dsv-status {
        height: 1;
        padding: 0 1;
        color: $text-muted;
    }
    DynamicSlotView #dsv-running-impl {
        layout: vertical;
        height: 1fr;
    }
    DynamicSlotView #dsv-footer {
        height: 1;
        padding: 0 1;
        color: $text-muted;
    }
    DynamicSlotView #dsv-post-run {
        height: auto;
        padding: 1;
        border: solid $primary;
    }
    DynamicSlotView #dsv-running-refine {
        layout: vertical;
        height: 1fr;
    }
    DynamicSlotView #dsv-refine-panes {
        layout: horizontal;
        height: 1fr;
    }
    DynamicSlotView .dsv-pane {
        width: 1fr;
        height: 1fr;
        border: solid $primary;
        padding: 1;
    }
    DynamicSlotView #dsv-review-title {
        height: 3;
        padding: 1;
        background: $surface;
        border-bottom: solid $primary;
    }
    """

    def __init__(
        self,
        slot: DynamicSlot,
        project_dir: Path,
        tracker: IssueTracker,
        item: Item,
        registry: DynamicSlotRegistry,
    ) -> None:
        super().__init__(id=f"view-slot-{slot.run_id}")
        self._slot = slot
        self._project_dir = project_dir
        self._tracker = tracker
        self._item = item
        self._registry = registry
        self._substate: _SubState = "running"
        self._worker: Worker[None] | None = None
        self._sink: StageCountingSink | None = None
        self._cumulative_cost = 0.0
        self._started_at = 0.0
        self._clock_interval: object = None
        self._chat_pane: ChatPaneWidget | None = None
        self._review_future: asyncio.Future[str] | None = None  # "accept" | "abandon"
        self._review_state: dict[str, str | Path] = {}
        self._split_pct: int = 50

    def compose(self) -> ComposeResult:
        yield Static("", id="dsv-status")
        # Implement running state
        impl_running = Container(id="dsv-running-impl")
        impl_running.display = False
        yield impl_running
        # Post-run panel (implement)
        post_run = Static("", id="dsv-post-run")
        post_run.display = False
        yield post_run
        # Refine running state (includes review via display toggle)
        refine_running = Container(id="dsv-running-refine")
        refine_running.display = False
        with refine_running:
            yield Static("", id="dsv-review-title")
            with Horizontal(id="dsv-refine-panes"):
                with ScrollableContainer(classes="dsv-pane", id="dsv-original"):
                    yield Static("", id="dsv-original-body")
                yield Container(classes="dsv-pane", id="dsv-right")

    def start_run(self) -> None:
        """Called by the shell after mounting. Starts the workflow in a worker."""
        self._worker = self.run_worker(self._run_workflow(), exclusive=True)

    def focus_content(self) -> None:
        if self._slot.workflow == "refine" and self._chat_pane is not None:
            try:
                from textual.widgets import TextArea

                self._chat_pane.query_one("#input-area", TextArea).focus()
            except Exception:  # noqa: BLE001
                pass
        else:
            self.focus()

    def busy_description(self) -> str | None:
        if self._substate == "running":
            wf = self._slot.workflow.capitalize()
            return f"{wf} #{self._slot.item_id}"
        return None

    # -------------------------------------------------------------------------
    # Workflow dispatch
    # -------------------------------------------------------------------------

    async def _run_workflow(self) -> None:
        if self._slot.workflow == "implement":
            await self._run_implement()
        else:
            await self._run_refine()

    # -------------------------------------------------------------------------
    # Implement (ralph) flow
    # -------------------------------------------------------------------------

    async def _run_implement(self) -> None:
        self._cumulative_cost = 0.0
        self._started_at = time.monotonic()
        self._registry.update_stage(self._slot.run_id, "build")
        self._set_status(f"implement running on #{self._item.item_id}")

        running = self.query_one("#dsv-running-impl", Container)
        await running.remove_children()
        log = LogPaneWidget()
        await running.mount(log)
        footer = Static(self._footer_text(), id="dsv-footer")
        await running.mount(footer)
        self._clock_interval = self.set_interval(1.0, self._refresh_footer)

        running.display = True

        self._sink = StageCountingSink(log, on_cost=self._add_cost)
        ctx = self._make_ctx(event_sink=self._sink)

        from cog.hosts.github import GitHubGitHost
        from cog.runners.claude_cli import ClaudeCliRunner
        from cog.runners.docker_sandbox import DockerSandbox
        from cog.workflows.ralph import RalphWorkflow

        sandbox = DockerSandbox(project_dir=self._project_dir)
        runner = ClaudeCliRunner(sandbox)
        host = GitHubGitHost(self._project_dir)
        workflow = RalphWorkflow(runner=runner, tracker=self._tracker, host=host)

        from cog.core.workflow import IterationOutcome, StageExecutor

        header: str
        try:
            results = await StageExecutor().run(workflow, ctx)
            if not results:
                header = "[yellow]No eligible items — already processed or deferred.[/yellow]"
            else:
                commits = sum(r.commits_created for r in results)
                it_outcome = IterationOutcome.success if commits > 0 else IterationOutcome.noop
                await workflow.iteration_end(ctx, it_outcome)
                header = (
                    f"[green]✓ Complete[/green] — ${self._cumulative_cost:.3f} · "
                    f"{self._elapsed()} total"
                )
        except asyncio.CancelledError:
            await workflow.iteration_end(ctx, IterationOutcome.exception)
            if self._sink is not None:
                self._sink.mark_running_stages_failed()
            header = "[yellow]Aborted[/yellow]"
            self._render_post_run(header, errored=False)
            self._switch_to("post_run", errored=False)
            raise
        except Exception as e:  # noqa: BLE001
            await workflow.iteration_end(ctx, IterationOutcome.error)
            if self._sink is not None:
                self._sink.mark_running_stages_failed()
            header = f"[red]✗ Failed:[/red] {e!s}"
            self._render_post_run(header, errored=True)
            self._switch_to("post_run", errored=True)
            return
        finally:
            if self._clock_interval is not None:
                self._clock_interval.stop()

        self._render_post_run(header, errored=False)
        self._switch_to("post_run", errored=False)
        self._set_status(f"implement finished on #{self._item.item_id}")

    def _render_post_run(self, header: str, *, errored: bool) -> None:
        stages = self._sink.stages if self._sink is not None else []
        breakdown = stage_breakdown_line(stages)
        body = f"{header}\n{breakdown}" if breakdown else header
        body += "\n\n[dim]Press Enter to dismiss.[/dim]"
        self.query_one("#dsv-post-run", Static).update(body)

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
            self.query_one("#dsv-footer", Static).update(self._footer_text())
        except Exception:  # noqa: BLE001
            pass

    # -------------------------------------------------------------------------
    # Refine flow
    # -------------------------------------------------------------------------

    async def _run_refine(self) -> None:
        self._registry.update_stage(self._slot.run_id, "interview")
        self._set_status(f"refine running on #{self._item.item_id}")

        running = self.query_one("#dsv-running-refine", Container)
        right = self.query_one("#dsv-right", Container)
        await right.remove_children()
        chat = ChatPaneWidget()
        self._chat_pane = chat
        await right.mount(chat)
        self._render_original_pane(self._item)

        title_strip = self.query_one("#dsv-review-title", Static)
        title_strip.display = False
        running.display = True

        ctx = self._make_ctx(
            event_sink=chat,
            input_provider=_SlotAttentionInputProvider(chat, self),
            review_provider=self,
        )

        from cog.runners.claude_cli import ClaudeCliRunner
        from cog.runners.docker_sandbox import DockerSandbox
        from cog.workflows.refine import RefineWorkflow

        sandbox = DockerSandbox()
        runner = ClaudeCliRunner(sandbox)
        workflow = RefineWorkflow(runner=runner, tracker=self._tracker)

        from cog.core.workflow import StageExecutor

        try:
            await StageExecutor().run(workflow, ctx)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            self._set_status(f"[red]refine failed on #{self._item.item_id}: {e}[/red]")

        # After workflow finishes (review done), close the slot
        self.post_message(SlotDismissed(self._slot.run_id))

    # ---- ReviewProvider protocol -----------------------------------------

    async def review(
        self,
        *,
        original_title: str,
        original_body: str,
        proposed_title: str,
        proposed_body: str,
        tmp_dir: Path,
    ) -> object:
        from cog.workflows.refine import ReviewDecision, ReviewOutcome

        self._review_state = {
            "original_title": original_title,
            "original_body": original_body,
            "proposed_title": proposed_title,
            "proposed_body": proposed_body,
            "tmp_dir": tmp_dir,
        }

        right = self.query_one("#dsv-right", Container)
        if self._chat_pane is not None:
            self._chat_pane.display = False

        proposed_scroll = ScrollableContainer(id="dsv-proposed-scroll")
        await right.mount(proposed_scroll)
        proposed = Static("", id="dsv-proposed-body")
        await proposed_scroll.mount(proposed)
        proposed.update(Markdown(str(proposed_body) or "*(empty)*"))

        self._render_review_title(original_title, proposed_title)
        self._switch_to("reviewing", errored=False)
        self._registry.update_stage(self._slot.run_id, "review")
        self._set_status(f"review ready — #{self._item.item_id}")

        self._review_future = asyncio.get_running_loop().create_future()
        try:
            decision_str = await self._review_future
        finally:
            self._review_future = None
            self.refresh_bindings()

        decision = ReviewDecision.ACCEPT if decision_str == "accept" else ReviewDecision.ABANDON
        return ReviewOutcome(
            decision=decision,
            final_body=str(self._review_state.get("proposed_body", "")),
            final_title=str(self._review_state.get("proposed_title", "")),
        )

    def _render_review_title(self, original: str, proposed: str) -> None:
        strip = self.query_one("#dsv-review-title", Static)
        strip.display = True
        if original == proposed:
            strip.update(f"Title: {original} [dim][unchanged][/dim]")
        else:
            strip.update(f"Title: {original} → [bold]{proposed}[/bold]")

    def _render_original_pane(self, item: Item) -> None:
        parts = [item.body or "*(empty body)*"]
        for comment in item.comments:
            ts = comment.created_at.strftime("%Y-%m-%d %H:%M")
            parts.append(f"\n---\n\n**@{comment.author}** · {ts}\n\n{comment.body}")
        self.query_one("#dsv-original-body", Static).update(Markdown("".join(parts)))

    def _resolve_review(self, decision: str) -> None:
        if self._review_future is None or self._review_future.done():
            return
        self._review_future.set_result(decision)

    # -------------------------------------------------------------------------
    # Actions
    # -------------------------------------------------------------------------

    def action_dismiss(self) -> None:
        if self._substate != "post_run":
            return
        self.post_message(SlotDismissed(self._slot.run_id))

    def action_review_accept(self) -> None:
        if self._substate != "reviewing":
            return
        self._resolve_review("accept")

    def action_review_abandon(self) -> None:
        if self._substate != "reviewing":
            return
        self._resolve_review("abandon")

    async def action_review_edit(self) -> None:
        if self._substate != "reviewing":
            return
        proposed = str(self._review_state.get("proposed_body", ""))
        tmp_dir = self._review_state.get("tmp_dir", Path(tempfile.gettempdir()))
        assert isinstance(tmp_dir, Path)
        from cog.ui.editor import suspend_and_edit

        edited = await suspend_and_edit(self.app, proposed, tmp_dir)
        if edited is not None:
            self._review_state["proposed_body"] = edited
            proposed_widget = self.query_one("#dsv-proposed-body", Static)
            proposed_widget.update(Markdown(edited or "*(empty)*"))
            self._render_review_title(
                str(self._review_state.get("original_title", "")),
                str(self._review_state.get("proposed_title", "")),
            )

    def action_abort(self) -> None:
        if self._substate != "running":
            return
        wf = self._slot.workflow
        item_id = self._slot.item_id
        if wf == "implement":
            msg = f"Abort implement #{item_id}? Worktree and branch will be cleaned up."
        else:
            msg = f"Abort refine #{item_id}? Interview state will be discarded."
        from cog.ui.screens.launch_confirm import LaunchConfirmScreen

        self.app.push_screen(LaunchConfirmScreen(msg), self._on_abort_confirmed)

    def _on_abort_confirmed(self, confirmed: bool | None) -> None:
        if not confirmed:
            return
        if self._worker is not None:
            self._worker.cancel()

    def action_narrow_pane(self) -> None:
        self._split_pct = max(20, self._split_pct - 5)
        self._apply_split()

    def action_widen_pane(self) -> None:
        self._split_pct = min(80, self._split_pct + 5)
        self._apply_split()

    def _apply_split(self) -> None:
        try:
            orig = self.query_one("#dsv-original", ScrollableContainer)
            orig.styles.width = f"{self._split_pct}%"
        except Exception:  # noqa: BLE001
            pass

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if action == "abort":
            return True if self._substate == "running" else None
        if action == "dismiss":
            return True if self._substate == "post_run" else None
        if action in ("review_accept", "review_edit", "review_abandon"):
            return (
                True if self._substate == "reviewing" and self._review_future is not None else None
            )
        if action in ("narrow_pane", "widen_pane"):
            return (
                True
                if self._substate in ("running", "reviewing") and self._slot.workflow == "refine"
                else None
            )
        return True

    # -------------------------------------------------------------------------
    # State transitions
    # -------------------------------------------------------------------------

    def _switch_to(self, substate: _SubState, *, errored: bool = False) -> None:
        self._substate = substate
        self.query_one("#dsv-running-impl", Container).display = (
            self._slot.workflow == "implement" and substate == "running"
        )
        self.query_one("#dsv-post-run", Static).display = (
            self._slot.workflow == "implement" and substate == "post_run"
        )
        self.query_one("#dsv-running-refine", Container).display = self._slot.workflow == "refine"
        self.refresh_bindings()
        self.call_after_refresh(self.focus_content)
        self._post_state_changed(errored=errored)

    def _post_state_changed(self, *, errored: bool = False) -> None:
        if self._substate == "running":
            state = "running"
        elif self._substate == "reviewing":
            state = "awaiting_dismiss"
        else:
            state = "awaiting_dismiss"
        stage = self._slot.stage
        self.post_message(SlotStateChanged(self._slot.run_id, state, stage, errored))  # type: ignore[arg-type]

    # -------------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------------

    def _make_ctx(self, **extra: object) -> ExecutionContext:
        state_dir = project_state_dir(self._project_dir)
        cache = JsonFileStateCache(state_dir / "state.json")
        cache.load()
        telemetry = TelemetryWriter(state_dir)
        tmp_dir = Path(tempfile.mkdtemp(prefix="cog-"))
        return ExecutionContext(
            project_dir=self._project_dir,
            tmp_dir=tmp_dir,
            state_cache=cache,
            headless=False,
            item=self._item,
            telemetry=telemetry,
            **extra,  # type: ignore[arg-type]
        )

    def _set_status(self, text: str) -> None:
        try:
            self.query_one("#dsv-status", Static).update(text)
        except Exception:  # noqa: BLE001
            pass
