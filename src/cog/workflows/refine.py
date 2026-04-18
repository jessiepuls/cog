"""RefineWorkflow stub. Full implementation in #18."""

from cog.core.context import ExecutionContext
from cog.core.item import Item
from cog.core.outcomes import Outcome, StageResult
from cog.core.stage import Stage
from cog.core.workflow import Workflow


class RefineWorkflow(Workflow):
    """Interactive refinement workflow. Stub: full implementation in #18."""

    name = "refine"
    queue_label = "needs-refinement"
    supports_headless = False
    # content_widget_cls = ChatPaneWidget  # wired in #18

    def __init__(self, **kwargs: object) -> None:
        self._kwargs = kwargs

    async def select_item(self, ctx: ExecutionContext) -> Item | None:
        raise NotImplementedError("RefineWorkflow.select_item implemented in #18")

    def stages(self, ctx: ExecutionContext) -> list[Stage]:
        raise NotImplementedError("RefineWorkflow.stages implemented in #18")

    async def classify_outcome(self, ctx: ExecutionContext, results: list[StageResult]) -> Outcome:
        raise NotImplementedError("RefineWorkflow.classify_outcome implemented in #18")
