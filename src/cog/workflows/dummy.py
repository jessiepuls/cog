from datetime import UTC, datetime

from cog.core.context import ExecutionContext
from cog.core.item import Item
from cog.core.outcomes import Outcome, StageResult
from cog.core.runner import AgentRunner
from cog.core.stage import Stage
from cog.core.workflow import Workflow


class DummyWorkflow(Workflow):
    name = "dummy"
    queue_label = "dummy"
    supports_headless = True

    def __init__(self, runner: AgentRunner) -> None:
        self._runner = runner

    async def select_item(self, ctx: ExecutionContext) -> Item | None:
        return Item(
            tracker_id="dummy",
            item_id="1",
            title="hello",
            body="",
            labels=(),
            comments=(),
            updated_at=datetime.now(UTC),
            url="",
        )

    def stages(self, ctx: ExecutionContext) -> list[Stage]:
        return [
            Stage(
                name="one",
                prompt_source=lambda _c: "hello from one",
                model="dummy",
                runner=self._runner,
            ),
            Stage(
                name="two",
                prompt_source=lambda _c: "hello from two",
                model="dummy",
                runner=self._runner,
            ),
        ]

    async def classify_outcome(self, ctx: ExecutionContext, results: list[StageResult]) -> Outcome:
        return "success"
