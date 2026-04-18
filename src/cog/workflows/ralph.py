"""RalphWorkflow: autonomous agent workflow."""

import re

import cog.git as git
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


class RalphWorkflow(Workflow):
    """Autonomous agent workflow."""

    name = "ralph"
    queue_label = "agent-ready"
    supports_headless = True

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
        raise NotImplementedError("RalphWorkflow.stages implemented in #12")

    async def classify_outcome(self, ctx: ExecutionContext, results: list[StageResult]) -> Outcome:
        raise NotImplementedError("RalphWorkflow.classify_outcome implemented in #12")
