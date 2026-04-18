"""RalphWorkflow stub. Full implementation in #12."""

from cog.core.context import ExecutionContext
from cog.core.item import Item
from cog.core.outcomes import Outcome, StageResult
from cog.core.stage import Stage
from cog.core.workflow import Workflow


class RalphWorkflow(Workflow):
    """Autonomous agent workflow. Stub: full implementation in #12."""

    name = "ralph"
    queue_label = "agent-ready"
    supports_headless = True
    # content_widget_cls = LogPaneWidget  # wired in #12

    def __init__(self, **kwargs: object) -> None:
        self._kwargs = kwargs

    async def select_item(self, ctx: ExecutionContext) -> Item | None:
        raise NotImplementedError("RalphWorkflow.select_item implemented in #12")

    def stages(self, ctx: ExecutionContext) -> list[Stage]:
        raise NotImplementedError("RalphWorkflow.stages implemented in #12")

    async def classify_outcome(self, ctx: ExecutionContext, results: list[StageResult]) -> Outcome:
        raise NotImplementedError("RalphWorkflow.classify_outcome implemented in #12")
