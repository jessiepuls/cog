"""Tests for RunScreen."""

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest
from textual.app import App, ComposeResult

from cog.core.context import ExecutionContext
from cog.core.item import Item
from cog.core.runner import ResultEvent, RunResult
from cog.core.stage import Stage
from cog.core.workflow import Workflow
from cog.ui.screens.run import RunScreen
from tests.fakes import EchoRunner, InMemoryStateCache, NullContentWidget


def _item() -> Item:
    return Item(
        tracker_id="gh",
        item_id="1",
        title="test",
        body="",
        labels=(),
        comments=(),
        state="open",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        url="",
    )


def _ctx(tmp_path: Path) -> ExecutionContext:
    return ExecutionContext(
        project_dir=tmp_path,
        tmp_dir=tmp_path / "tmp",
        state_cache=InMemoryStateCache(),
        headless=False,
        item=_item(),
    )


def _dummy_result() -> RunResult:
    return RunResult(
        final_message="",
        total_cost_usd=0.0,
        exit_status=0,
        stream_json_path=Path("/dev/null"),
        duration_seconds=0.0,
    )


class _SlowRunner:
    """Runner that blocks until cancelled — used to keep the workflow in 'running' state."""

    def stream(self, prompt: str, *, model: str):  # type: ignore[return]
        return self._slow_stream()

    async def _slow_stream(self):
        await asyncio.sleep(10)
        yield ResultEvent(result=_dummy_result())

    async def run(self, prompt: str, *, model: str) -> None:  # pragma: no cover
        pass


class _FakeWorkflow(Workflow):
    name = "fake"
    queue_label = "fake-label"
    supports_headless = True
    content_widget_cls = NullContentWidget

    def __init__(self, runner: EchoRunner) -> None:
        self._runner = runner

    async def select_item(self, ctx: ExecutionContext) -> Item | None:
        return _item()

    def stages(self, ctx: ExecutionContext):
        return [Stage(name="s1", prompt_source=lambda _: "hi", model="m", runner=self._runner)]

    async def classify_outcome(self, ctx, results):
        return "success"


class _SlowWorkflow(_FakeWorkflow):
    """Workflow whose single stage blocks indefinitely — for testing running-state behavior."""

    def stages(self, ctx):
        return [Stage(name="s", prompt_source=lambda _: "hi", model="m", runner=_SlowRunner())]


def _make_app(workflow: Workflow, ctx: ExecutionContext) -> App:
    class _TestApp(App):
        def compose(self) -> ComposeResult:
            return iter([])

        def on_mount(self) -> None:
            self.push_screen(RunScreen(workflow, ctx))

    return _TestApp()


async def test_run_screen_hides_q_quit_binding_while_textarea_focused(tmp_path: Path) -> None:
    # Regression: during the refine interview, focus is on a TextArea where
    # `q` is a printable char — the footer's "q Quit / back" hint is misleading.
    from textual.widgets import TextArea

    class _ChatContent(NullContentWidget):
        def compose(self):
            yield TextArea(id="chat-input")

    class _ChatWorkflow(_FakeWorkflow):
        content_widget_cls = _ChatContent

    workflow = _ChatWorkflow(EchoRunner())
    ctx = _ctx(tmp_path)

    async with _make_app(workflow, ctx).run_test(headless=True) as pilot:
        await pilot.pause()
        area = pilot.app.query_one("#chat-input", TextArea)
        area.focus()
        await pilot.pause()
        screen = pilot.app.query_one(RunScreen)
        assert screen.check_action("quit_or_return", ()) is None


async def test_run_screen_shows_q_quit_binding_when_textarea_not_focused(tmp_path: Path) -> None:
    workflow = _FakeWorkflow(EchoRunner())
    ctx = _ctx(tmp_path)

    async with _make_app(workflow, ctx).run_test(headless=True) as pilot:
        await pilot.pause()
        screen = pilot.app.query_one(RunScreen)
        assert screen.check_action("quit_or_return", ()) is True


async def test_run_screen_mounts_content_widget_for_each_workflow(tmp_path: Path) -> None:
    workflow = _FakeWorkflow(EchoRunner())
    ctx = _ctx(tmp_path)
    async with _make_app(workflow, ctx).run_test(headless=True) as pilot:
        await pilot.pause()
        pilot.app.query_one(NullContentWidget)  # raises if absent


async def test_run_screen_sets_event_sink_to_content_widget(tmp_path: Path) -> None:
    workflow = _FakeWorkflow(EchoRunner())
    ctx = _ctx(tmp_path)
    async with _make_app(workflow, ctx).run_test(headless=True) as pilot:
        await pilot.pause()
        assert ctx.event_sink is not None


async def test_run_screen_sets_input_provider_for_chat_only(tmp_path: Path) -> None:
    # NullContentWidget has no `prompt` → input_provider stays None
    workflow = _FakeWorkflow(EchoRunner())
    ctx = _ctx(tmp_path)
    async with _make_app(workflow, ctx).run_test(headless=True) as pilot:
        await pilot.pause()
        assert ctx.input_provider is None


async def test_run_screen_clock_advances(tmp_path: Path) -> None:
    workflow = _FakeWorkflow(EchoRunner())
    ctx = _ctx(tmp_path)
    async with _make_app(workflow, ctx).run_test(headless=True) as pilot:
        await pilot.pause()
        screen = pilot.app.query_one(RunScreen)
        assert screen._started_at > 0.0


async def test_run_screen_cost_accumulates_from_result_events(tmp_path: Path) -> None:
    from collections.abc import AsyncIterator

    from cog.core.runner import AgentRunner, RunEvent

    class _CostRunner(AgentRunner):
        """Runner that yields a ResultEvent with non-zero cost."""

        async def stream(self, prompt: str, *, model: str) -> AsyncIterator[RunEvent]:
            yield ResultEvent(
                result=RunResult(
                    final_message="done",
                    total_cost_usd=0.05,
                    exit_status=0,
                    stream_json_path=Path("/dev/null"),
                    duration_seconds=1.0,
                )
            )

    workflow = _FakeWorkflow(_CostRunner())
    ctx = _ctx(tmp_path)
    async with _make_app(workflow, ctx).run_test(headless=True) as pilot:
        for _ in range(5):
            await pilot.pause()
        screen = pilot.app.query_one(RunScreen)
        assert screen._state == "completed"
        assert screen._cumulative_cost == pytest.approx(0.05)


async def test_run_screen_ctrl_c_cancels_worker(tmp_path: Path) -> None:
    """Pressing ctrl+c while running transitions to cancelled."""
    workflow = _SlowWorkflow(EchoRunner())
    ctx = _ctx(tmp_path)
    async with _make_app(workflow, ctx).run_test(headless=True) as pilot:
        await pilot.pause()
        screen = pilot.app.query_one(RunScreen)
        assert screen._state == "running"
        await pilot.press("ctrl+c")
        # Give cancellation time to propagate through the worker
        for _ in range(10):
            await pilot.pause()
        assert screen._state == "cancelled"


async def test_run_screen_q_ignored_while_running(tmp_path: Path) -> None:
    workflow = _SlowWorkflow(EchoRunner())
    ctx = _ctx(tmp_path)
    async with _make_app(workflow, ctx).run_test(headless=True) as pilot:
        await pilot.pause()
        screen = pilot.app.query_one(RunScreen)
        assert screen._state == "running"
        await pilot.press("q")
        await pilot.pause()
        # Still on RunScreen (q ignored while running)
        assert pilot.app.query_one(RunScreen) is not None


async def test_run_screen_q_pops_after_completion(tmp_path: Path) -> None:
    workflow = _FakeWorkflow(EchoRunner())
    ctx = _ctx(tmp_path)
    async with _make_app(workflow, ctx).run_test(headless=True) as pilot:
        # Wait for workflow to complete
        for _ in range(5):
            await pilot.pause()
        screen = pilot.app.query_one(RunScreen)
        assert screen._state == "completed"
        await pilot.press("q")
        await pilot.pause()
        # RunScreen has been popped (app exited or at base screen)
        assert pilot.app.screen is not screen


async def test_run_screen_shows_error_panel_on_workflow_exception(tmp_path: Path) -> None:
    class _BrokenWorkflow(_FakeWorkflow):
        def stages(self, ctx):
            async def _err_stream(prompt, *, model):
                raise RuntimeError("kaboom")
                yield  # make it a generator

            class _ErrRunner:
                def stream(self, prompt, *, model):
                    return _err_stream(prompt, model=model)

                async def run(self, prompt, *, model):  # pragma: no cover
                    pass

            return [Stage(name="s", prompt_source=lambda _: "hi", model="m", runner=_ErrRunner())]

    workflow = _BrokenWorkflow(EchoRunner())
    ctx = _ctx(tmp_path)
    async with _make_app(workflow, ctx).run_test(headless=True) as pilot:
        for _ in range(5):
            await pilot.pause()
        screen = pilot.app.query_one(RunScreen)
        assert screen._state == "failed"
        assert len(pilot.app.query("#result-panel")) > 0


class _MultiItemWorkflow(_FakeWorkflow):
    """Workflow that serves N items then returns None (queue empty)."""

    def __init__(self, runner: EchoRunner, item_count: int) -> None:
        super().__init__(runner)
        self._remaining = item_count

    async def select_item(self, ctx: ExecutionContext) -> Item | None:
        if self._remaining > 0:
            self._remaining -= 1
            return _item()
        return None


def _make_loop_app(
    workflow: Workflow,
    ctx: ExecutionContext,
    *,
    loop: bool = True,
    max_iterations: int | None = None,
) -> App:
    # Loop-mode tests exercise select_item end-to-end, so the base ctx must not
    # carry a pre-selected item (which fresh_iteration_context preserves on iter 1).
    ctx.item = None

    class _TestApp(App):
        def compose(self) -> ComposeResult:
            return iter([])

        def on_mount(self) -> None:
            self.push_screen(RunScreen(workflow, ctx, loop=loop, max_iterations=max_iterations))

    return _TestApp()


async def test_run_screen_preselected_item_skips_select_item_on_iter_1(tmp_path: Path) -> None:
    # Regression: main-menu flow (and `cog --item N`) pre-populates base_ctx.item.
    # RunScreen must preserve that item on iteration 1 — otherwise the workflow's
    # select_item runs and, for RefineWorkflow with >1 candidates and no ItemPicker,
    # raises "refine requires an ItemPicker".
    class _RecordingWorkflow(_FakeWorkflow):
        def __init__(self, runner: EchoRunner) -> None:
            super().__init__(runner)
            self.select_item_calls = 0

        async def select_item(self, ctx: ExecutionContext) -> Item | None:
            self.select_item_calls += 1
            return _item()

    workflow = _RecordingWorkflow(EchoRunner())
    ctx = _ctx(tmp_path)  # _ctx pre-populates ctx.item
    assert ctx.item is not None  # sanity

    class _TestApp(App):
        def compose(self) -> ComposeResult:
            return iter([])

        def on_mount(self) -> None:
            self.push_screen(RunScreen(workflow, ctx, loop=False, max_iterations=1))

    async with _TestApp().run_test(headless=True) as pilot:
        for _ in range(10):
            await pilot.pause()

    assert workflow.select_item_calls == 0, (
        "base_ctx.item was pre-set; select_item should not run on iteration 1"
    )


async def test_run_screen_loop_mode_iterates_multiple_times(tmp_path: Path) -> None:
    workflow = _MultiItemWorkflow(EchoRunner(), item_count=3)
    ctx = _ctx(tmp_path)
    async with _make_loop_app(workflow, ctx).run_test(headless=True) as pilot:
        for _ in range(20):
            await pilot.pause()
        screen = pilot.app.query_one(RunScreen)
        assert screen._state == "completed"
        assert screen._loop_state.iteration == 3


async def test_run_screen_loop_mode_divider_between_iterations(tmp_path: Path) -> None:
    workflow = _MultiItemWorkflow(EchoRunner(), item_count=2)
    ctx = _ctx(tmp_path)
    async with _make_loop_app(workflow, ctx).run_test(headless=True) as pilot:
        for _ in range(20):
            await pilot.pause()
        dividers = pilot.app.query(".iteration-divider")
        assert len(dividers) >= 1


async def test_run_screen_loop_mode_iteration_counter_in_footer(tmp_path: Path) -> None:
    workflow = _MultiItemWorkflow(EchoRunner(), item_count=2)
    ctx = _ctx(tmp_path)
    async with _make_loop_app(workflow, ctx).run_test(headless=True) as pilot:
        for _ in range(20):
            await pilot.pause()
        screen = pilot.app.query_one(RunScreen)
        assert screen._loop_state.iteration == 2


async def test_run_screen_loop_mode_exits_on_queue_empty(tmp_path: Path) -> None:
    workflow = _MultiItemWorkflow(EchoRunner(), item_count=1)
    ctx = _ctx(tmp_path)
    async with _make_loop_app(workflow, ctx).run_test(headless=True) as pilot:
        for _ in range(20):
            await pilot.pause()
        screen = pilot.app.query_one(RunScreen)
        assert screen._state == "completed"


async def test_run_screen_loop_mode_respects_max_iterations(tmp_path: Path) -> None:
    workflow = _MultiItemWorkflow(EchoRunner(), item_count=10)
    ctx = _ctx(tmp_path)
    async with _make_loop_app(workflow, ctx, loop=True, max_iterations=2).run_test(
        headless=True
    ) as pilot:
        for _ in range(20):
            await pilot.pause()
        screen = pilot.app.query_one(RunScreen)
        assert screen._state == "completed"
        assert screen._loop_state.iteration == 2


async def test_run_screen_loop_mode_ctrl_c_cancels_current_iteration(tmp_path: Path) -> None:
    workflow = _MultiItemWorkflow(_SlowRunner(), item_count=5)
    ctx = _ctx(tmp_path)
    async with _make_loop_app(workflow, ctx).run_test(headless=True) as pilot:
        await pilot.pause()
        screen = pilot.app.query_one(RunScreen)
        assert screen._state == "running"
        await pilot.press("ctrl+c")
        for _ in range(10):
            await pilot.pause()
        assert screen._state == "cancelled"


async def test_run_screen_loop_mode_stage_error_shows_error_panel_and_exits(
    tmp_path: Path,
) -> None:
    class _ErrWorkflow(_FakeWorkflow):
        def stages(self, ctx: ExecutionContext):
            async def _err(prompt: str, *, model: str):
                raise RuntimeError("stage boom")
                yield  # type: ignore[misc]

            class _R:
                def stream(self, prompt, *, model):
                    return _err(prompt, model=model)

                async def run(self, prompt, *, model):  # pragma: no cover
                    pass

            return [Stage(name="s", prompt_source=lambda _: "hi", model="m", runner=_R())]

    workflow = _ErrWorkflow(EchoRunner())
    ctx = _ctx(tmp_path)
    async with _make_loop_app(workflow, ctx).run_test(headless=True) as pilot:
        for _ in range(10):
            await pilot.pause()
        screen = pilot.app.query_one(RunScreen)
        assert screen._state == "failed"
        assert len(pilot.app.query("#result-panel")) > 0


async def test_run_screen_loop_mode_completion_panel_after_clean_exit(tmp_path: Path) -> None:
    workflow = _MultiItemWorkflow(EchoRunner(), item_count=1)
    ctx = _ctx(tmp_path)
    async with _make_loop_app(workflow, ctx).run_test(headless=True) as pilot:
        for _ in range(20):
            await pilot.pause()
        screen = pilot.app.query_one(RunScreen)
        assert screen._state == "completed"
        assert len(pilot.app.query("#result-panel")) > 0


async def test_run_screen_shows_cancellation_panel_after_cancel(tmp_path: Path) -> None:
    # Verify that _show_cancellation_panel mounts the result panel when called
    # while the screen is still attached. (During real ctrl+c, the screen may
    # already be detached — tested in test_run_screen_ctrl_c_cancels_worker.)
    workflow = _FakeWorkflow(EchoRunner())
    ctx = _ctx(tmp_path)
    async with _make_app(workflow, ctx).run_test(headless=True) as pilot:
        for _ in range(5):
            await pilot.pause()
        screen = pilot.app.query_one(RunScreen)
        assert screen._state == "completed"
        # Now manually trigger the cancellation panel while screen is attached
        screen._state = "cancelled"
        screen._show_cancellation_panel()
        await pilot.pause()
        assert len(pilot.app.query("#result-panel")) > 0
