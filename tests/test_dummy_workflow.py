"""End-to-end test: DummyWorkflow + EchoRunner through StageExecutor."""

from cog.core.workflow import StageExecutor
from cog.workflows.dummy import DummyWorkflow
from tests.fakes import EchoRunner


async def test_dummy_workflow_returns_two_stage_results(ctx_factory):
    runner = EchoRunner()
    wf = DummyWorkflow(runner)
    results = await StageExecutor().run(wf, ctx_factory())
    assert len(results) == 2
    assert results[0].final_message == "hello from one"
    assert results[1].final_message == "hello from two"


async def test_dummy_workflow_selects_item(ctx_factory):
    wf = DummyWorkflow(EchoRunner())
    ctx = ctx_factory()
    assert ctx.item is None
    await StageExecutor().run(wf, ctx)
    assert ctx.item is not None
    assert ctx.item.title == "hello"


async def test_dummy_workflow_outcome_is_success(ctx_factory):
    """classify_outcome always returns success for DummyWorkflow."""
    wf = DummyWorkflow(EchoRunner())
    ctx = ctx_factory()
    results = await StageExecutor().run(wf, ctx)
    # If outcome were noop, finalize_noop would be called — success path completes cleanly
    assert len(results) == 2
    assert all(r.exit_status == 0 for r in results)
