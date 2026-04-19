from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass
from enum import Enum
from importlib.resources import files
from typing import ClassVar

from cog.checks import REFINE_CHECKS
from cog.core.context import ExecutionContext
from cog.core.errors import WorkflowError
from cog.core.item import Item
from cog.core.outcomes import Outcome, StageResult
from cog.core.runner import AgentRunner, ResultEvent
from cog.core.stage import Stage
from cog.core.tracker import IssueTracker
from cog.core.workflow import Workflow
from cog.state_paths import project_slug
from cog.telemetry import StageTelemetry, TelemetryRecord
from cog.ui.widgets.chat_pane import ChatPaneWidget

_INTERVIEW_COMPLETE = "<<interview-complete>>"


class InterviewEnd(Enum):
    NOT_ENDED = "not-ended"
    SENTINEL = "sentinel"
    USER = "user"


@dataclass(frozen=True)
class InterviewTurn:
    assistant_message: str
    user_message: str | None
    cost_usd: float
    duration_seconds: float
    end: InterviewEnd = InterviewEnd.NOT_ENDED


class RefineWorkflow(Workflow):
    name: ClassVar[str] = "refine"
    queue_label: ClassVar[str] = "needs-refinement"
    supports_headless: ClassVar[bool] = False
    needs_item_picker: ClassVar[bool] = True
    preflight_checks = REFINE_CHECKS
    content_widget_cls = ChatPaneWidget

    def __init__(self, runner: AgentRunner, tracker: IssueTracker, **kwargs: object) -> None:
        self._runner = runner
        self._tracker = tracker
        self._transcripts: dict[str, list[InterviewTurn]] = {}

    async def select_item(self, ctx: ExecutionContext) -> Item | None:
        items = await self._tracker.list_by_label("needs-refinement", assignee="@me")
        if not items:
            return None
        items.sort(key=lambda i: i.created_at)
        if len(items) == 1:
            return items[0]
        if ctx.item_picker is None:
            raise WorkflowError(
                "refine requires an ItemPicker (only runs in Textual mode with --item-picker wired)"
            )
        return await ctx.item_picker.pick(items)

    async def pre_stages(self, ctx: ExecutionContext) -> None:
        assert ctx.item is not None, "refine pre_stages requires ctx.item"
        assert ctx.event_sink is not None, "refine interview requires an event sink"
        assert ctx.input_provider is not None, "refine interview requires an input provider"
        transcript = await self._run_interview(ctx)
        self._transcripts[ctx.item.item_id] = transcript

    def stages(self, ctx: ExecutionContext) -> list[Stage]:
        raise NotImplementedError("rewrite stage lands with #19")

    async def classify_outcome(self, ctx: ExecutionContext, results: list[StageResult]) -> Outcome:
        raise NotImplementedError("refine classify_outcome lands with #19")

    async def finalize_error(
        self, ctx: ExecutionContext, error: Exception, results: list[StageResult]
    ) -> None:
        item_ref = ctx.item.item_id if ctx.item else "?"
        print(
            f"refine: interview failed on #{item_ref}: {type(error).__name__}: {error}",
            file=sys.stderr,
        )
        if ctx.item is None or ctx.telemetry is None:
            return
        transcript = self._transcripts.get(ctx.item.item_id, [])
        if not transcript:
            return
        model = os.environ.get("COG_REFINE_INTERVIEW_MODEL", "claude-sonnet-4-6")
        interview_stage = self._interview_telemetry_stage(transcript, model)
        record = TelemetryRecord.build(
            project=project_slug(ctx.project_dir),
            workflow=self.name,
            item=ctx.item,
            outcome="error",
            results=[],
            extra_stages=(interview_stage,),
            duration_seconds=sum(t.duration_seconds for t in transcript),
            error=str(error),
        )
        await ctx.telemetry.write(record)

    def _build_preamble(self, item: Item) -> str:
        skill = (
            files("cog.prompts.claude.refine").joinpath("interview.md").read_text(encoding="utf-8")
        )
        parts = [skill, "\n## Item to refine\n", f"Issue #{item.item_id}: {item.title}"]
        parts.append(f"\n### Body\n\n{item.body}\n")
        if item.comments:
            parts.append("\n### Comments\n")
            for c in item.comments:
                parts.append(f"\n**{c.author}** ({c.created_at.isoformat()}):\n{c.body}\n")
        return "\n".join(parts)

    def _build_turn_prompt(self, preamble: str, transcript: list[InterviewTurn]) -> str:
        parts = [preamble]
        if transcript:
            parts.append("\n## Conversation so far\n")
            for turn in transcript:
                parts.append(f"\n### You:\n{turn.assistant_message}\n")
                if turn.user_message is not None:
                    parts.append(f"\n### User:\n{turn.user_message}\n")
        parts.append(
            f"\n## Your turn\n\nAsk your next question, or if you have all the "
            f"information you need to rewrite the item body, output the token "
            f"`{_INTERVIEW_COMPLETE}` on its own line."
        )
        return "\n".join(parts)

    def _interview_telemetry_stage(
        self, transcript: list[InterviewTurn], model: str
    ) -> StageTelemetry:
        return StageTelemetry(
            stage="interview",
            model=model,
            duration_s=sum(t.duration_seconds for t in transcript),
            cost_usd=sum(t.cost_usd for t in transcript),
            exit_status=0,
            commits=0,
        )

    async def _run_interview(self, ctx: ExecutionContext) -> list[InterviewTurn]:
        assert ctx.event_sink is not None, "refine interview requires an event sink"
        assert ctx.input_provider is not None, "refine interview requires an input provider"
        assert ctx.item is not None
        transcript: list[InterviewTurn] = []
        preamble = self._build_preamble(ctx.item)
        model = os.environ.get("COG_REFINE_INTERVIEW_MODEL", "claude-sonnet-4-6")
        while True:
            prompt = self._build_turn_prompt(preamble, transcript)
            start = time.monotonic()
            total_cost = 0.0
            final_message = ""
            async for event in self._runner.stream(prompt, model=model):
                if isinstance(event, ResultEvent):
                    total_cost = event.result.total_cost_usd
                    final_message = event.result.final_message
                    continue
                await ctx.event_sink.emit(event)
            duration = time.monotonic() - start
            if _INTERVIEW_COMPLETE in final_message:
                cleaned = final_message.replace(_INTERVIEW_COMPLETE, "").strip()
                transcript.append(
                    InterviewTurn(
                        assistant_message=cleaned,
                        user_message=None,
                        cost_usd=total_cost,
                        duration_seconds=duration,
                        end=InterviewEnd.SENTINEL,
                    )
                )
                return transcript
            user_reply = await ctx.input_provider.prompt()
            if user_reply is None:
                transcript.append(
                    InterviewTurn(
                        assistant_message=final_message,
                        user_message=None,
                        cost_usd=total_cost,
                        duration_seconds=duration,
                        end=InterviewEnd.USER,
                    )
                )
                return transcript
            transcript.append(
                InterviewTurn(
                    assistant_message=final_message,
                    user_message=user_reply,
                    cost_usd=total_cost,
                    duration_seconds=duration,
                    end=InterviewEnd.NOT_ENDED,
                )
            )
