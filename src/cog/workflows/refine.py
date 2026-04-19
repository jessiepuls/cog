from __future__ import annotations

import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from importlib.resources import files
from typing import ClassVar, Literal

from cog.checks import REFINE_CHECKS
from cog.core.context import ExecutionContext
from cog.core.errors import WorkflowError
from cog.core.item import Item
from cog.core.outcomes import Outcome, StageResult
from cog.core.runner import AgentRunner, ResultEvent
from cog.core.stage import Stage
from cog.core.tracker import IssueTracker
from cog.core.workflow import Workflow
from cog.state_paths import project_slug, project_state_dir
from cog.telemetry import StageTelemetry, TelemetryRecord
from cog.ui.widgets.chat_pane import ChatPaneWidget

_INTERVIEW_COMPLETE = "<<interview-complete>>"
_SLUG_RE = re.compile(r"[^a-z0-9-]+")
_COLLAPSE_RE = re.compile(r"-+")
_SECTION_RE = re.compile(
    r"^###\s+(Title|Body)\s*\n(.*?)(?=\n###\s|\Z)",
    re.MULTILINE | re.DOTALL | re.IGNORECASE,
)


def _extract_title_body(message: str) -> dict[str, str]:
    sections: dict[str, str] = {}
    for match in _SECTION_RE.finditer(message):
        key = match.group(1).lower()
        sections[key] = match.group(2).strip()
    return sections


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


class ReviewDecision(Enum):
    ACCEPT = "accept"
    ABANDON = "abandon"


@dataclass(frozen=True)
class ReviewOutcome:
    decision: ReviewDecision
    final_body: str
    final_title: str


def _load_refine_prompt(name: str) -> str:
    return files("cog.prompts.claude.refine").joinpath(f"{name}.md").read_text(encoding="utf-8")


def _slugify(title: str) -> str:
    lower = title.lower()
    replaced = _SLUG_RE.sub("-", lower)
    collapsed = _COLLAPSE_RE.sub("-", replaced).strip("-")
    capped = collapsed[:50].rstrip("-")
    return capped or "issue"


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
        self._review_outcomes: dict[str, ReviewOutcome] = {}

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
        return [
            Stage(
                name="rewrite",
                prompt_source=lambda c: self._build_rewrite_prompt(c),
                model=os.environ.get("COG_REFINE_REWRITE_MODEL", "claude-opus-4-6"),
                runner=self._runner,
                tolerate_failure=False,
            )
        ]

    async def post_stages(self, ctx: ExecutionContext, results: list[StageResult]) -> None:
        assert ctx.item is not None
        assert ctx.app is not None, "refine post_stages requires ctx.app (Textual mode only)"

        from cog.ui.screens.review import ReviewScreen

        rewrite_result = next(r for r in results if r.stage.name == "rewrite")
        sections = _extract_title_body(rewrite_result.final_message)
        proposed_title = (sections.get("title") or ctx.item.title).strip() or ctx.item.title
        proposed_body = (sections.get("body") or rewrite_result.final_message).strip()

        screen = ReviewScreen(
            original_title=ctx.item.title,
            original_body=ctx.item.body,
            proposed_title=proposed_title,
            proposed_body=proposed_body,
            tmp_dir=ctx.tmp_dir,
        )
        outcome: ReviewOutcome = await ctx.app.push_screen_wait(screen)
        self._review_outcomes[ctx.item.item_id] = outcome

    async def classify_outcome(self, ctx: ExecutionContext, results: list[StageResult]) -> Outcome:
        assert ctx.item is not None
        outcome = self._review_outcomes[ctx.item.item_id]
        return "success" if outcome.decision == ReviewDecision.ACCEPT else "noop"

    async def finalize_success(self, ctx: ExecutionContext, results: list[StageResult]) -> None:
        assert ctx.item is not None and ctx.telemetry is not None
        review = self._review_outcomes[ctx.item.item_id]
        transcript = self._transcripts[ctx.item.item_id]

        title_changed = review.final_title != ctx.item.title
        await self._tracker.update_body(
            ctx.item,
            review.final_body,
            title=review.final_title if title_changed else None,
        )

        await self._tracker.remove_label(ctx.item, "needs-refinement")
        await self._tracker.add_label(ctx.item, "agent-ready")
        if transcript[-1].end == InterviewEnd.USER:
            await self._tracker.ensure_label(
                "partially-refined",
                color="fbca04",
                description="Refinement interview ended early; review before implementing.",
            )
            await self._tracker.add_label(ctx.item, "partially-refined")

        model = os.environ.get("COG_REFINE_INTERVIEW_MODEL", "claude-sonnet-4-6")
        interview_stage = self._interview_telemetry_stage(transcript, model)
        record = TelemetryRecord.build(
            project=project_slug(ctx.project_dir),
            workflow=self.name,
            item=ctx.item,
            outcome="success",
            results=results,
            extra_stages=(interview_stage,),
            duration_seconds=(
                sum(t.duration_seconds for t in transcript)
                + sum(r.duration_seconds for r in results)
            ),
        )
        await ctx.telemetry.write(record)
        await self._write_report(ctx, results, transcript, review, outcome="success")

    async def finalize_noop(self, ctx: ExecutionContext, results: list[StageResult]) -> None:
        assert ctx.item is not None
        transcript = self._transcripts.get(ctx.item.item_id, [])
        review = self._review_outcomes.get(ctx.item.item_id)

        await self._tracker.comment(
            ctx.item,
            "🤖 Cog ran a refinement interview but you chose not to apply the "
            "proposed rewrite. The `needs-refinement` label is preserved; run "
            "`cog refine --item <N>` to try again.",
        )

        if ctx.telemetry is not None and transcript:
            model = os.environ.get("COG_REFINE_INTERVIEW_MODEL", "claude-sonnet-4-6")
            interview_stage = self._interview_telemetry_stage(transcript, model)
            record = TelemetryRecord.build(
                project=project_slug(ctx.project_dir),
                workflow=self.name,
                item=ctx.item,
                outcome="no-op",
                results=results,
                extra_stages=(interview_stage,),
                duration_seconds=(
                    sum(t.duration_seconds for t in transcript)
                    + sum(r.duration_seconds for r in results)
                ),
            )
            await ctx.telemetry.write(record)

        if review is not None:
            await self._write_report(ctx, results, transcript, review, outcome="noop")

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

    async def _write_report(
        self,
        ctx: ExecutionContext,
        results: list[StageResult],
        transcript: list[InterviewTurn],
        review: ReviewOutcome,
        outcome: Literal["success", "noop"],
    ) -> None:
        assert ctx.item is not None
        ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        item_slug = f"{ctx.item.item_id}-{_slugify(ctx.item.title)}"
        reports_dir = project_state_dir(ctx.project_dir) / "reports"
        reports_dir.mkdir(parents=True, exist_ok=True)
        report_path = reports_dir / f"{ts}-refine-{item_slug}.md"

        lines: list[str] = [f"# Refine Report: {ctx.item.title} (#{ctx.item.item_id})\n"]
        outcome_label = "success" if outcome == "success" else "no-op (abandoned)"
        lines.append(f"**Outcome**: {outcome_label}\n")

        if outcome == "success":
            label_info = "needs-refinement → agent-ready"
            if transcript and transcript[-1].end == InterviewEnd.USER:
                label_info += " + partially-refined"
            lines.append(f"**Labels applied**: {label_info}\n")

        lines.append("## Title change\n")
        if review.final_title == ctx.item.title:
            lines.append(f"- No change: {ctx.item.title}\n")
        else:
            lines.append(f"- **Before**: {ctx.item.title}")
            lines.append(f"- **After**: {review.final_title}\n")

        lines.append("## Body before\n")
        lines.append(f"```\n{ctx.item.body}\n```\n")

        lines.append("## Body after\n")
        if outcome == "noop":
            lines.append("```\n(Abandoned; body unchanged)\n```\n")
        else:
            lines.append(f"```\n{review.final_body}\n```\n")

        lines.append("## Interview transcript\n")
        n = len(transcript)
        end_via = transcript[-1].end.value if transcript else "n/a"
        lines.append(f"{n} turn(s). Ended via: {end_via}.\n")
        for i, turn in enumerate(transcript, 1):
            lines.append(f"### Turn {i} — Assistant\n{turn.assistant_message}\n")
            if turn.user_message is not None:
                lines.append(f"### Turn {i} — User\n{turn.user_message}\n")

        lines.append("## Stages\n")
        lines.append("| Stage | Model | Duration (s) | Cost ($) | Exit |")
        lines.append("|-------|-------|--------------|----------|------|")
        model = os.environ.get("COG_REFINE_INTERVIEW_MODEL", "claude-sonnet-4-6")
        if transcript:
            dur = sum(t.duration_seconds for t in transcript)
            cost = sum(t.cost_usd for t in transcript)
            lines.append(f"| interview | {model} | {dur:.1f} | {cost:.4f} | 0 |")
        for r in results:
            lines.append(
                f"| {r.stage.name} | {r.stage.model} | {r.duration_seconds:.1f}"
                f" | {r.cost_usd:.4f} | {r.exit_status} |"
            )
        lines.append("")

        total_cost = sum(t.cost_usd for t in transcript) + sum(r.cost_usd for r in results)
        total_dur = sum(t.duration_seconds for t in transcript) + sum(
            r.duration_seconds for r in results
        )
        lines.append(f"**Total cost**: ${total_cost:.4f} | **Duration**: {total_dur:.1f}s")

        report_path.write_text("\n".join(lines), encoding="utf-8")

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

    def _build_rewrite_prompt(self, ctx: ExecutionContext) -> str:
        assert ctx.item is not None
        transcript = self._transcripts[ctx.item.item_id]
        item = ctx.item
        parts = [
            _load_refine_prompt("rewrite"),
            "\n## Item being refined\n",
            f"Issue #{item.item_id}: {item.title}",
            f"\n### Original body\n\n{item.body}\n",
        ]
        if item.comments:
            parts.append("\n### Comments\n")
            for c in item.comments:
                parts.append(f"\n**{c.author}**:\n{c.body}\n")
        parts.append("\n## Interview transcript\n")
        for i, turn in enumerate(transcript, 1):
            parts.append(f"\n### Turn {i} — Assistant\n{turn.assistant_message}\n")
            if turn.user_message is not None:
                parts.append(f"\n### Turn {i} — User\n{turn.user_message}\n")
        if transcript[-1].end == InterviewEnd.USER:
            parts.append(
                "\n## Refinement status\n\n"
                "⚠ The user ended the interview early. Some design decisions may "
                "be unresolved; see the 'Early-end handling' section above.\n"
            )
        parts.append("\n## Your turn\n\nProduce the final rewritten item now.")
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
