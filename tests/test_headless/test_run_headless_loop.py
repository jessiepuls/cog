"""Tests for run_headless loop/max_iterations behavior."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

from cog.core.context import ExecutionContext
from cog.core.item import Item
from cog.core.outcomes import Outcome, StageResult
from cog.core.runner import AgentRunner, ResultEvent, RunEvent, RunResult
from cog.core.stage import Stage
from cog.core.workflow import Workflow
from cog.headless import run_headless
from tests.fakes import EchoRunner, InMemoryStateCache


def _now() -> datetime:
    return datetime.now(UTC)


def _make_item(item_id: str = "1") -> Item:
    return Item(
        tracker_id="test",
        item_id=item_id,
        title=f"item-{item_id}",
        body="",
        labels=(),
        comments=(),
        state="open",
        created_at=_now(),
        updated_at=_now(),
        url="",
    )


def _make_ctx(tmp_path: Path) -> ExecutionContext:
    return ExecutionContext(
        project_dir=tmp_path,
        tmp_dir=tmp_path / "tmp",
        state_cache=InMemoryStateCache(),
        headless=True,
    )


class _QueueWorkflow(Workflow):
    """Workflow that pops items from a queue; returns None when exhausted."""

    name = "queue"
    queue_label = "test"
    supports_headless = True

    def __init__(self, runner: AgentRunner, items: list[Item]) -> None:
        self._runner = runner
        self._items = list(items)
        self.select_calls = 0

    async def select_item(self, ctx: ExecutionContext) -> Item | None:
        self.select_calls += 1
        return self._items.pop(0) if self._items else None

    def stages(self, ctx: ExecutionContext) -> list[Stage]:
        return [Stage(name="s", prompt_source=lambda _: "hi", model="m", runner=self._runner)]

    async def classify_outcome(self, ctx: ExecutionContext, results: list[StageResult]) -> Outcome:
        return "success"


class _CostRunner(AgentRunner):
    def __init__(self, cost: float) -> None:
        self._cost = cost

    async def stream(self, prompt: str, *, model: str) -> AsyncIterator[RunEvent]:
        yield ResultEvent(
            result=RunResult(
                final_message="ok",
                total_cost_usd=self._cost,
                exit_status=0,
                stream_json_path=Path("/dev/null"),
                duration_seconds=1.0,
            )
        )


async def test_single_run_mode_runs_once_even_when_queue_has_more(tmp_path: Path, capsys) -> None:
    items = [_make_item("1"), _make_item("2"), _make_item("3")]
    wf = _QueueWorkflow(EchoRunner(), items)
    exit_code = await run_headless(wf, _make_ctx(tmp_path), loop=False)
    assert exit_code == 0
    assert wf.select_calls == 1


async def test_loop_continues_until_queue_empty(tmp_path: Path, capsys) -> None:
    items = [_make_item("1"), _make_item("2"), _make_item("3")]
    wf = _QueueWorkflow(EchoRunner(), items)
    exit_code = await run_headless(wf, _make_ctx(tmp_path), loop=True)
    assert exit_code == 0
    # 3 items + 1 empty-queue call = 4 select_item calls
    assert wf.select_calls == 4


async def test_loop_respects_max_iterations(tmp_path: Path, capsys) -> None:
    items = [_make_item(str(i)) for i in range(5)]
    wf = _QueueWorkflow(EchoRunner(), items)
    exit_code = await run_headless(wf, _make_ctx(tmp_path), loop=True, max_iterations=2)
    assert exit_code == 0
    assert wf.select_calls == 2


async def test_loop_cumulative_cost_sums_across_iterations(tmp_path: Path, capsys) -> None:
    items = [_make_item("1"), _make_item("2")]
    runner = _CostRunner(cost=0.1)
    wf = _QueueWorkflow(runner, items)
    await run_headless(wf, _make_ctx(tmp_path), loop=True)
    captured = capsys.readouterr()
    assert "total cost $0.200" in captured.err


async def test_loop_iteration_counter_increments(tmp_path: Path, capsys) -> None:
    items = [_make_item("1"), _make_item("2")]
    wf = _QueueWorkflow(EchoRunner(), items)
    await run_headless(wf, _make_ctx(tmp_path), loop=True)
    captured = capsys.readouterr()
    assert "2 iteration(s)" in captured.err


async def test_loop_workflow_instance_reused(tmp_path: Path) -> None:
    """Regression guard: same workflow object is reused across iterations."""
    items = [_make_item("1"), _make_item("2")]
    wf = _QueueWorkflow(EchoRunner(), items)
    await run_headless(wf, _make_ctx(tmp_path), loop=True)
    # Both items consumed from the same workflow instance
    assert len(wf._items) == 0


async def test_loop_stage_error_aborts_loop_with_exit_1(tmp_path: Path, capsys) -> None:
    from collections.abc import AsyncIterator

    from cog.core.runner import AgentRunner, RunEvent

    class _FailRunner(AgentRunner):
        async def stream(self, prompt: str, *, model: str) -> AsyncIterator[RunEvent]:
            yield ResultEvent(
                result=RunResult(
                    final_message="failed",
                    total_cost_usd=0.0,
                    exit_status=1,
                    stream_json_path=Path("/dev/null"),
                    duration_seconds=0.0,
                )
            )

    items = [_make_item("1"), _make_item("2"), _make_item("3")]
    wf = _QueueWorkflow(_FailRunner(), items)
    exit_code = await run_headless(wf, _make_ctx(tmp_path), loop=True)
    assert exit_code == 1
    # First iteration fails; subsequent iterations never run
    assert wf.select_calls == 1


async def test_loop_banner_emitted_per_iteration(tmp_path: Path, capsys) -> None:
    items = [_make_item("1"), _make_item("2"), _make_item("3")]
    wf = _QueueWorkflow(EchoRunner(), items)
    await run_headless(wf, _make_ctx(tmp_path), loop=True)
    captured = capsys.readouterr()
    assert "═══ iteration 1 ═══" in captured.err
    assert "═══ iteration 2 ═══" in captured.err
    assert "═══ iteration 3 ═══" in captured.err


async def test_loop_final_summary_on_clean_exit(tmp_path: Path, capsys) -> None:
    items = [_make_item("1")]
    wf = _QueueWorkflow(EchoRunner(), items)
    await run_headless(wf, _make_ctx(tmp_path), loop=True)
    captured = capsys.readouterr()
    assert "loop complete: 1 iteration(s)" in captured.err


async def test_loop_no_final_summary_in_single_run_mode(tmp_path: Path, capsys) -> None:
    items = [_make_item("1")]
    wf = _QueueWorkflow(EchoRunner(), items)
    await run_headless(wf, _make_ctx(tmp_path), loop=False)
    captured = capsys.readouterr()
    assert "loop complete" not in captured.err


async def test_loop_fresh_context_per_iteration(tmp_path: Path) -> None:
    """Each iteration gets a fresh ctx (item/work_branch reset); state_cache persists."""
    seen_items: list[Item | None] = []
    seen_caches: list[object] = []

    class _ObservingWorkflow(Workflow):
        name = "obs"
        queue_label = "test"
        supports_headless = True

        def __init__(self) -> None:
            self._calls = 0

        async def select_item(self, ctx: ExecutionContext) -> Item | None:
            self._calls += 1
            seen_caches.append(ctx.state_cache)
            seen_items.append(ctx.item)
            return _make_item(str(self._calls)) if self._calls <= 2 else None

        def stages(self, ctx: ExecutionContext) -> list[Stage]:
            return [
                Stage(
                    name="s",
                    prompt_source=lambda _: "hi",
                    model="m",
                    runner=EchoRunner(),
                )
            ]

        async def classify_outcome(
            self, ctx: ExecutionContext, results: list[StageResult]
        ) -> Outcome:
            return "success"

    wf = _ObservingWorkflow()
    ctx = _make_ctx(tmp_path)
    await run_headless(wf, ctx, loop=True)
    # item is always None at start of each iteration (reset by fresh_iteration_context)
    assert all(item is None for item in seen_items)
    # state_cache is the same object across iterations
    assert all(cache is ctx.state_cache for cache in seen_caches)
