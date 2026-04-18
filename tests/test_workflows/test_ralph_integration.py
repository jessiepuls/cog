"""Integration test: RalphWorkflow runs all three stages via StageExecutor."""

from cog.core.context import ExecutionContext
from cog.core.workflow import StageExecutor
from cog.workflows.ralph import RalphWorkflow
from tests.fakes import EchoRunner, InMemoryStateCache, make_item


async def test_runs_all_three_stages_end_to_end(tmp_path):
    runner = EchoRunner()
    wf = RalphWorkflow(runner)

    ctx = ExecutionContext(
        project_dir=tmp_path,
        tmp_dir=tmp_path,
        state_cache=InMemoryStateCache(),
        headless=True,
        item=make_item(),  # pre-select item, bypassing select_item
        work_branch="ralph/42-test",
    )

    results = await StageExecutor().run(wf, ctx)

    assert len(results) == 3
    assert [r.stage.name for r in results] == ["build", "review", "document"]
    assert all(r.exit_status == 0 for r in results)
    assert all(r.error is None for r in results)
