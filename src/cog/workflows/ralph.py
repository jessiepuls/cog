from __future__ import annotations

import os
from collections.abc import Callable
from importlib.resources import files
from typing import ClassVar

from cog.checks import RALPH_CHECKS
from cog.core.context import ExecutionContext
from cog.core.item import Item
from cog.core.outcomes import Outcome, StageResult
from cog.core.runner import AgentRunner
from cog.core.stage import Stage
from cog.core.workflow import Workflow


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
    name: ClassVar[str] = "ralph"
    queue_label: ClassVar[str] = "agent-ready"
    supports_headless: ClassVar[bool] = True
    preflight_checks = RALPH_CHECKS

    def __init__(self, runner: AgentRunner) -> None:
        self._runner = runner

    async def select_item(self, ctx: ExecutionContext) -> Item | None:
        raise NotImplementedError(
            "RalphWorkflow queue selection lands with #13. "
            "For v1 testing, pre-select via `cog ralph --item <N>`."
        )

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
