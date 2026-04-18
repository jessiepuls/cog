"""RalphWorkflow: autonomous agent workflow."""

from __future__ import annotations

import os
import re
import sys
from collections.abc import Callable
from importlib.resources import files
from typing import TYPE_CHECKING, ClassVar, Literal

import cog.git as git
from cog.checks import RALPH_CHECKS
from cog.core.context import ExecutionContext
from cog.core.errors import HostError, StageError
from cog.core.host import GitHost
from cog.core.item import Item
from cog.core.outcomes import Outcome, StageResult
from cog.core.runner import AgentRunner
from cog.core.stage import Stage
from cog.core.tracker import IssueTracker
from cog.core.workflow import Workflow
from cog.state_paths import project_slug, project_state_dir
from cog.telemetry import TelemetryRecord

if TYPE_CHECKING:
    pass

_PRIORITY_RE = re.compile(r"^p(\d+)$")
_SLUG_RE = re.compile(r"[^a-z0-9-]+")
_COLLAPSE_RE = re.compile(r"-+")
_TEST_PLAN_RE = re.compile(
    r"###\s+Test plan\s*\n(.*?)(?=\n###|\Z)",
    re.DOTALL | re.IGNORECASE,
)


def _priority_tier(item: Item) -> int:
    """Extract lowest pN tier from labels. Items without pN go to tier 999."""
    tiers = [int(m.group(1)) for label in item.labels if (m := _PRIORITY_RE.match(label))]
    return min(tiers) if tiers else 999


def _slugify(title: str) -> str:
    lower = title.lower()
    replaced = _SLUG_RE.sub("-", lower)
    collapsed = _COLLAPSE_RE.sub("-", replaced).strip("-")
    capped = collapsed[:50].rstrip("-")
    return capped or "issue"


def _load_prompt(stage_name: str) -> str:
    return (
        files("cog.prompts.claude.ralph").joinpath(f"{stage_name}.md").read_text(encoding="utf-8")
    )


def _build_prompt(stage_name: str, ctx: ExecutionContext) -> str:
    if ctx.item is None:
        raise AssertionError("RalphWorkflow requires ctx.item to be set")
    item = ctx.item
    parts: list[str] = [_load_prompt(stage_name)]
    parts.append("\n## Your task this iteration\n")
    parts.append(f"Issue #{item.item_id}: {item.title}")
    if ctx.work_branch:
        parts.append(f"Branch: {ctx.work_branch}")
    parts.append(f"\n### Issue body\n\n{item.body}\n")
    if item.comments:
        comments_formatted = "\n\n".join(
            f"**{c.author}** ({c.created_at.isoformat()}):\n{c.body}" for c in item.comments
        )
        parts.append(f"\n### Comments\n\n{comments_formatted}\n")
    return "\n".join(parts)


def _make_prompt_source(stage_name: str) -> Callable[[ExecutionContext], str]:
    return lambda ctx: _build_prompt(stage_name, ctx)


def _split_summary_and_test_plan(message: str) -> tuple[str, str]:
    m = _TEST_PLAN_RE.search(message)
    if m is None:
        return message.strip(), "- [ ] Manual verification of the change"
    summary = message[: m.start()].strip()
    test_plan = m.group(1).strip()
    return summary, test_plan


class RalphWorkflow(Workflow):
    """Autonomous agent workflow."""

    name: ClassVar[str] = "ralph"
    queue_label: ClassVar[str] = "agent-ready"
    supports_headless: ClassVar[bool] = True
    preflight_checks = RALPH_CHECKS

    def __init__(
        self,
        runner: AgentRunner,
        tracker: IssueTracker,
        host: GitHost | None = None,
    ) -> None:
        self._runner = runner
        self._tracker = tracker
        self._host = host
        self._processed_this_loop: set[tuple[str, str]] = set()

    async def select_item(self, ctx: ExecutionContext) -> Item | None:
        items = await self._tracker.list_by_label("agent-ready", assignee="@me")
        eligible = [
            item
            for item in items
            if (item.tracker_id, item.item_id) not in self._processed_this_loop
            and not ctx.state_cache.is_processed(item)
            and not ctx.state_cache.is_deferred(item)
        ]
        if not eligible:
            return None
        eligible.sort(key=lambda i: (_priority_tier(i), i.created_at))
        chosen = eligible[0]
        self._processed_this_loop.add((chosen.tracker_id, chosen.item_id))
        return chosen

    async def pre_stages(self, ctx: ExecutionContext) -> None:
        assert ctx.item is not None, "RalphWorkflow.pre_stages requires ctx.item"
        default = await git.default_branch(ctx.project_dir)
        await git.checkout_branch(ctx.project_dir, default)
        await git.fetch_origin(ctx.project_dir)
        await git.merge_ff_only(ctx.project_dir, f"origin/{default}")
        work_branch = f"cog/{ctx.item.item_id}-{_slugify(ctx.item.title)}"
        await git.create_branch(ctx.project_dir, work_branch, start_point="HEAD")
        ctx.work_branch = work_branch

    def stages(self, ctx: ExecutionContext) -> list[Stage]:
        return [
            Stage(
                name="build",
                prompt_source=_make_prompt_source("build"),
                model=os.environ.get("COG_RALPH_BUILD_MODEL", "claude-sonnet-4-6"),
                runner=self._runner,
                tolerate_failure=False,
            ),
            Stage(
                name="review",
                prompt_source=_make_prompt_source("review"),
                model=os.environ.get("COG_RALPH_REVIEW_MODEL", "claude-opus-4-6"),
                runner=self._runner,
                tolerate_failure=False,
            ),
            Stage(
                name="document",
                prompt_source=_make_prompt_source("document"),
                model=os.environ.get("COG_RALPH_DOCUMENT_MODEL", "claude-sonnet-4-6"),
                runner=self._runner,
                # document failures flagged for PR footer by #14; don't abort
                tolerate_failure=True,
            ),
        ]

    async def classify_outcome(self, ctx: ExecutionContext, results: list[StageResult]) -> Outcome:
        total_commits = sum(r.commits_created for r in results)
        return "success" if total_commits > 0 else "noop"

    async def finalize_success(
        self,
        ctx: ExecutionContext,
        results: list[StageResult],
    ) -> None:
        assert ctx.item is not None and ctx.work_branch is not None
        assert self._host is not None, "RalphWorkflow.finalize_success requires host"
        try:
            await self._host.push_branch(ctx.work_branch)
        except HostError as e:
            await self._handle_push_failed(ctx, results, e)
            return

        existing = await self._host.get_pr_for_branch(ctx.work_branch)
        body = self._build_pr_body(ctx, results)
        if existing is None:
            title = f"{ctx.item.title} (#{ctx.item.item_id})"
            pr = await self._host.create_pr(head=ctx.work_branch, title=title, body=body)
        else:
            await self._host.update_pr(existing.number, body=body)
            pr = existing

        await self._tracker.comment(ctx.item, f"🤖 Cog opened a PR for this: {pr.url}")
        await self._tracker.remove_label(ctx.item, "agent-ready")
        ctx.state_cache.mark_processed(ctx.item, "success")
        ctx.state_cache.save()
        await self._write_telemetry(ctx, results, "success", pr_url=pr.url)
        await self.write_report(ctx, results, "success", error=None)

    async def finalize_noop(
        self,
        ctx: ExecutionContext,
        results: list[StageResult],
    ) -> None:
        assert ctx.item is not None
        build = next((r for r in results if r.stage.name == "build"), None)
        explanation = (
            build.final_message
            if build and build.final_message.strip()
            else "(no explanation provided)"
        )
        await self._tracker.comment(
            ctx.item,
            f"🤖 Cog attempted this but did not commit.\n\n{explanation}",
        )
        await self._tracker.ensure_label(
            "agent-abandoned",
            color="ededed",
            description="Cog attempted this but made no changes",
        )
        await self._tracker.add_label(ctx.item, "agent-abandoned")
        await self._tracker.remove_label(ctx.item, "agent-ready")
        ctx.state_cache.mark_processed(ctx.item, "no-op")
        ctx.state_cache.save()
        await self._write_telemetry(ctx, results, "no-op")
        await self.write_report(ctx, results, "noop", error=None)

    async def finalize_error(
        self,
        ctx: ExecutionContext,
        error: Exception,
        results: list[StageResult],
    ) -> None:
        assert ctx.item is not None
        if isinstance(error, StageError):
            summary = f"Stage '{error.stage.name}' failed"
            tail = (
                error.result.final_message[-2000:]
                if error.result and error.result.final_message
                else ""
            )
        else:
            summary = f"{type(error).__name__}: {error}"
            tail = ""
        body = f"🤖 Cog encountered an error:\n\n```\n{summary}\n```"
        if tail:
            body += f"\n\nLast output:\n\n```\n{tail}\n```"
        try:
            await self._tracker.comment(ctx.item, body)
        except Exception:
            print(
                f"warning: failed to comment error on #{ctx.item.item_id}",
                file=sys.stderr,
            )
        # KEEP agent-ready label — user fixes infra, ralph retries next run.
        # Don't mark processed — item stays eligible.
        await self._write_telemetry(ctx, results, "error", error=str(error))
        await self.write_report(ctx, results, "error", error=error)

    async def write_report(
        self,
        ctx: ExecutionContext,
        results: list[StageResult],
        outcome: Literal["success", "noop", "error"],
        *,
        error: Exception | None = None,
    ) -> None:
        assert ctx.item is not None
        from datetime import UTC, datetime

        ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        item_slug = f"{ctx.item.item_id}-{_slugify(ctx.item.title)}"
        reports_dir = project_state_dir(ctx.project_dir) / "reports"
        reports_dir.mkdir(parents=True, exist_ok=True)
        report_path = reports_dir / f"{ts}-ralph-{item_slug}.md"

        build = next((r for r in results if r.stage.name == "build"), None)
        summary, test_plan = _split_summary_and_test_plan(build.final_message if build else "")

        pr_url: str | None = None
        if outcome == "success" and ctx.work_branch and self._host is not None:
            try:
                pr = await self._host.get_pr_for_branch(ctx.work_branch)
                if pr is not None:
                    pr_url = pr.url
            except Exception:
                pass

        total_cost = sum(r.cost_usd for r in results)
        total_duration = sum(r.duration_seconds for r in results)

        lines: list[str] = [f"# Ralph Report: {ctx.item.title} (#{ctx.item.item_id})\n"]
        if pr_url:
            lines.append(f"**PR:** {pr_url}\n")
        lines.append(f"## Summary\n\n{summary}\n")
        lines.append(f"## Test plan\n\n{test_plan}\n")
        lines.append("## Stages\n")
        lines.append("| Stage | Model | Duration (s) | Cost ($) | Exit | Commits | Error |")
        lines.append("|-------|-------|-------------|----------|------|---------|-------|")
        for r in results:
            err_str = str(r.error) if r.error else ""
            lines.append(
                f"| {r.stage.name} | {r.stage.model} | {r.duration_seconds:.1f}"
                f" | {r.cost_usd:.4f} | {r.exit_status} | {r.commits_created}"
                f" | {err_str} |"
            )
        lines.append("")

        if ctx.work_branch:
            try:
                default = await git.default_branch(ctx.project_dir)
                shas = await git.log_short_shas(
                    ctx.project_dir,
                    f"origin/{default}..HEAD",
                )
                if shas:
                    lines.append("## Commits\n")
                    lines.extend(f"- {sha}" for sha in shas)
                    lines.append("")
            except Exception:
                pass

        lines.append(f"## Outcome\n\n**{outcome}**")
        if error:
            lines.append(f"\nError: {error}")
        lines.append(f"\nTotal cost: ${total_cost:.4f} | Duration: {total_duration:.1f}s")

        report_path.write_text("\n".join(lines), encoding="utf-8")

    def _build_pr_body(self, ctx: ExecutionContext, results: list[StageResult]) -> str:
        build = next((r for r in results if r.stage.name == "build"), None)
        summary, test_plan = _split_summary_and_test_plan(build.final_message if build else "")
        total_cost = sum(r.cost_usd for r in results)
        body = (
            f"## Summary\n\n{summary}\n\n"
            f"## Closes\n\nCloses #{ctx.item.item_id}\n\n"  # type: ignore[union-attr]
            f"## Test plan\n\n{test_plan}\n\n"
            f"---\n🤖 Generated by cog. Iteration cost: ${total_cost:.3f}\n"
        )
        doc = next((r for r in results if r.stage.name == "document"), None)
        if doc is not None and doc.error is not None:
            body += f"\n⚠ Document stage failed: {doc.error}. Docs may be out of date.\n"
        return body

    async def _handle_push_failed(
        self,
        ctx: ExecutionContext,
        results: list[StageResult],
        error: HostError,
    ) -> None:
        assert ctx.item is not None and ctx.work_branch is not None
        body = (
            f"🤖 Cog finished stages but failed to push `{ctx.work_branch}`.\n\n"
            f"Error:\n```\n{error}\n```\n\n"
            f"Work is on your local branch. To push manually:\n"
            f"```\ngit push -u origin {ctx.work_branch}\n```"
        )
        try:
            await self._tracker.comment(ctx.item, body)
        except Exception:
            print(
                f"warning: failed to comment push-fail on #{ctx.item.item_id}",
                file=sys.stderr,
            )
        try:
            await self._tracker.remove_label(ctx.item, "agent-ready")
        except Exception:
            pass
        # Don't mark processed — local commits exist; next run either reuses the
        # branch (v1.1 orphan-resume) or fails cleanly on create_branch (v1).
        await self._write_telemetry(ctx, results, "push-failed", error=str(error))
        await self.write_report(ctx, results, "error", error=error)

    async def _write_telemetry(
        self,
        ctx: ExecutionContext,
        results: list[StageResult],
        outcome: str,
        *,
        pr_url: str | None = None,
        error: str | None = None,
    ) -> None:
        if ctx.telemetry is None:
            return
        record = TelemetryRecord.build(
            project=project_slug(ctx.project_dir),
            workflow=self.name,
            item=ctx.item,  # type: ignore[arg-type]
            outcome=outcome,  # type: ignore[arg-type]
            results=results,
            branch=ctx.work_branch,
            pr_url=pr_url,
            duration_seconds=sum(r.duration_seconds for r in results),
            error=error,
        )
        await ctx.telemetry.write(record)
