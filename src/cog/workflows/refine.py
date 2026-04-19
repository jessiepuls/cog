from typing import ClassVar

from cog.checks import REFINE_CHECKS
from cog.core.context import ExecutionContext
from cog.core.errors import WorkflowError
from cog.core.item import Item
from cog.core.outcomes import Outcome, StageResult
from cog.core.runner import AgentRunner
from cog.core.stage import Stage
from cog.core.tracker import IssueTracker
from cog.core.workflow import Workflow
from cog.ui.widgets.chat_pane import ChatPaneWidget


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

    def stages(self, ctx: ExecutionContext) -> list[Stage]:
        raise NotImplementedError("refine stages land with #18 (interview)")

    async def classify_outcome(self, ctx: ExecutionContext, results: list[StageResult]) -> Outcome:
        raise NotImplementedError("refine classify_outcome lands with #19 (rewrite)")
