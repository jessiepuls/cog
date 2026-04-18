"""RalphWorkflow: autonomous agent workflow."""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from importlib.resources import files
from typing import ClassVar

import cog.git as git
from cog.checks import RALPH_CHECKS
from cog.core.context import ExecutionContext
from cog.core.item import Item
from cog.core.outcomes import Outcome, StageResult
from cog.core.runner import AgentRunner
from cog.core.stage import Stage
from cog.core.tracker import IssueTracker
from cog.core.workflow import Workflow

_PRIORITY_RE = re.compile(r"^p(\d+)$")
_SLUG_RE = re.compile(r"[^a-z0-9-]+")
_COLLAPSE_RE = re.compile(r"-+")


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


class RalphWorkflow(Workflow):
    """Autonomous agent workflow."""

    name: ClassVar[str] = "ralph"
    queue_label: ClassVar[str] = "agent-ready"
    supports_headless: ClassVar[bool] = True
    preflight_checks = RALPH_CHECKS

    def __init__(self, runner: AgentRunner, tracker: IssueTracker) -> None:
        self._runner = runner
        self._tracker = tracker
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
