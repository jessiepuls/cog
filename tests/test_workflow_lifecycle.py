"""Tests for Workflow lifecycle hook ordering."""

from datetime import UTC, datetime

from cog.core.context import ExecutionContext
from cog.core.item import Item
from cog.core.outcomes import Outcome, StageResult
from cog.core.stage import Stage
from cog.core.workflow import StageExecutor, Workflow
from tests.fakes import EchoRunner


def _make_item() -> Item:
    return Item(
        tracker_id="test",
        item_id="1",
        title="lifecycle test",
        body="",
        labels=(),
        comments=(),
        updated_at=datetime.now(UTC),
        url="",
    )


class _RecordingWorkflow(Workflow):
    """Records every hook call in order."""

    name = "recording"
    queue_label = "test"
    supports_headless = True

    def __init__(self, runner: EchoRunner, *, outcome: Outcome = "success") -> None:
        self._runner = runner
        self._outcome = outcome
        self.log: list[str] = []

    async def select_item(self, ctx: ExecutionContext) -> Item | None:
        self.log.append("select_item")
        return _make_item()

    async def pre_stages(self, ctx: ExecutionContext) -> None:
        self.log.append("pre_stages")

    def stages(self, ctx: ExecutionContext) -> list[Stage]:
        self.log.append("stages")
        return [
            Stage(name="s1", prompt_source=lambda _: "p1", model="m", runner=self._runner),
            Stage(name="s2", prompt_source=lambda _: "p2", model="m", runner=self._runner),
        ]

    async def post_stages(self, ctx: ExecutionContext, results: list[StageResult]) -> None:
        self.log.append("post_stages")

    async def classify_outcome(self, ctx: ExecutionContext, results: list[StageResult]) -> Outcome:
        self.log.append("classify_outcome")
        return self._outcome

    async def finalize_success(self, ctx: ExecutionContext, results: list[StageResult]) -> None:
        self.log.append("finalize_success")

    async def finalize_noop(self, ctx: ExecutionContext, results: list[StageResult]) -> None:
        self.log.append("finalize_noop")


async def test_success_hook_order(ctx_factory, echo_runner):
    wf = _RecordingWorkflow(echo_runner, outcome="success")
    results = await StageExecutor().run(wf, ctx_factory())
    assert wf.log == [
        "select_item",
        "pre_stages",
        "stages",
        "post_stages",
        "classify_outcome",
        "finalize_success",
    ]
    # Verify both stages actually ran between stages() and post_stages()
    assert len(results) == 2
    assert results[0].final_message == "p1"
    assert results[1].final_message == "p2"


async def test_noop_hook_order(ctx_factory, echo_runner):
    wf = _RecordingWorkflow(echo_runner, outcome="noop")
    await StageExecutor().run(wf, ctx_factory())
    assert wf.log == [
        "select_item",
        "pre_stages",
        "stages",
        "post_stages",
        "classify_outcome",
        "finalize_noop",
    ]
