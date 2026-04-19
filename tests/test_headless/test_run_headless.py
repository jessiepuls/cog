"""Tests for run_headless."""

import re
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

from cog.core.context import ExecutionContext
from cog.core.item import Item
from cog.core.outcomes import Outcome, StageResult
from cog.core.runner import AgentRunner, ResultEvent, RunEvent, RunResult
from cog.core.stage import Stage
from cog.core.workflow import Workflow
from cog.headless import StderrEventSink, run_headless
from tests.fakes import EchoRunner, InMemoryStateCache


def _make_item() -> Item:
    now = datetime.now(UTC)
    return Item(
        tracker_id="test",
        item_id="1",
        title="test",
        body="",
        labels=(),
        comments=(),
        state="open",
        created_at=now,
        updated_at=now,
        url="",
    )


def _make_ctx(tmp_path: Path) -> ExecutionContext:
    return ExecutionContext(
        project_dir=tmp_path,
        tmp_dir=tmp_path / "tmp",
        state_cache=InMemoryStateCache(),
        headless=True,
    )


class _SimpleWorkflow(Workflow):
    name = "simple"
    queue_label = "test"
    supports_headless = True

    def __init__(self, runner: AgentRunner) -> None:
        self._runner = runner

    async def select_item(self, ctx: ExecutionContext) -> Item | None:
        return _make_item()

    def stages(self, ctx: ExecutionContext) -> list[Stage]:
        return [Stage(name="s1", prompt_source=lambda _: "hello", model="m", runner=self._runner)]

    async def classify_outcome(self, ctx: ExecutionContext, results: list[StageResult]) -> Outcome:
        return "success"


class _EmptyQueueWorkflow(_SimpleWorkflow):
    async def select_item(self, ctx: ExecutionContext) -> Item | None:
        return None


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


class _ExplodingWorkflow(_SimpleWorkflow):
    async def pre_stages(self, ctx: ExecutionContext) -> None:
        raise ValueError("boom")


async def test_run_headless_happy_path(tmp_path, capsys):
    wf = _SimpleWorkflow(EchoRunner())
    exit_code = await run_headless(wf, _make_ctx(tmp_path))
    assert exit_code == 0
    captured = capsys.readouterr()
    assert "outcome=success" in captured.err


async def test_run_headless_empty_queue(tmp_path, capsys):
    wf = _EmptyQueueWorkflow(EchoRunner())
    exit_code = await run_headless(wf, _make_ctx(tmp_path))
    assert exit_code == 0
    captured = capsys.readouterr()
    assert "outcome=no-op" in captured.err


async def test_run_headless_stage_error_returns_1(tmp_path, capsys):
    wf = _SimpleWorkflow(_FailRunner())
    exit_code = await run_headless(wf, _make_ctx(tmp_path))
    assert exit_code == 1
    captured = capsys.readouterr()
    assert "iteration FAILED: stage 's1' failed" in captured.err


async def test_run_headless_unexpected_exception_returns_1(tmp_path, capsys):
    wf = _ExplodingWorkflow(EchoRunner())
    exit_code = await run_headless(wf, _make_ctx(tmp_path))
    assert exit_code == 1
    captured = capsys.readouterr()
    assert "iteration FAILED: ValueError: boom" in captured.err


async def test_run_headless_sets_event_sink_to_stderr_sink(tmp_path):
    wf = _SimpleWorkflow(EchoRunner())
    ctx = _make_ctx(tmp_path)
    assert ctx.event_sink is None
    await run_headless(wf, ctx)
    assert isinstance(ctx.event_sink, StderrEventSink)


async def test_run_headless_leaves_input_provider_none(tmp_path):
    wf = _SimpleWorkflow(EchoRunner())
    ctx = _make_ctx(tmp_path)
    await run_headless(wf, ctx)
    # ExecutionContext has no input_provider field yet; just verify no side-effects
    assert not hasattr(ctx, "input_provider") or ctx.input_provider is None  # type: ignore[attr-defined]


async def test_run_headless_summary_format_regex(tmp_path, capsys):
    wf = _SimpleWorkflow(EchoRunner())
    await run_headless(wf, _make_ctx(tmp_path))
    captured = capsys.readouterr()
    pattern = r"iteration complete: outcome=\w+(?:-\w+)*, cost=\$\d+\.\d+, duration=\d+s"
    assert re.search(pattern, captured.err), f"Pattern not found in: {captured.err!r}"
