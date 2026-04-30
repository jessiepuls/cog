"""Tests for RefineWorkflow interview loop."""

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from cog.core.context import ExecutionContext
from cog.core.item import Comment, Item
from cog.core.runner import AssistantTextEvent, ResultEvent, StageEndEvent
from cog.workflows.refine import (
    _INTERVIEW_COMPLETE,
    InterviewEnd,
    InterviewTurn,
    RefineWorkflow,
)
from tests.fakes import (
    InMemoryStateCache,
    RecordingEventSink,
    ScriptedInputProvider,
    ScriptedInterviewRunner,
    make_item,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_workflow(responses: list[tuple[str, float]]) -> RefineWorkflow:
    runner = ScriptedInterviewRunner(responses)
    tracker = AsyncMock()
    return RefineWorkflow(runner=runner, tracker=tracker)


def _make_ctx(
    *,
    item: Item | None = None,
    sink: RecordingEventSink | None = None,
    provider: ScriptedInputProvider | None = None,
    tmp_path: Path,
) -> ExecutionContext:
    tmp_dir = tmp_path / "tmp"
    tmp_dir.mkdir(exist_ok=True)
    return ExecutionContext(
        project_dir=tmp_path,
        tmp_dir=tmp_dir,
        state_cache=InMemoryStateCache(),
        headless=False,
        item=item,
        event_sink=sink,
        input_provider=provider,
    )


def _make_item_with_comments() -> Item:
    return make_item(
        item_id="5",
        title="Add caching",
        body="We need caching.",
        comments=(
            Comment(
                author="alice",
                body="Good idea",
                created_at=datetime(2024, 1, 2, tzinfo=UTC),
            ),
        ),
    )


# ---------------------------------------------------------------------------
# Prompt assembly
# ---------------------------------------------------------------------------


def test_load_interview_prompt_reads_package_data():
    from importlib.resources import files

    text = files("cog.prompts.claude.refine").joinpath("interview.md").read_text(encoding="utf-8")
    assert "<<interview-complete>>" in text
    assert "Ask ONE question" in text


def test_preamble_includes_interview_prompt_plus_item_details():
    wf = _make_workflow([("hello", 0.0)])
    item = make_item(item_id="3", title="My issue", body="Some body")
    preamble = wf._build_preamble(item)
    assert "<<interview-complete>>" in preamble
    assert "Issue #3: My issue" in preamble
    assert "Some body" in preamble


def test_preamble_omits_comments_when_empty():
    wf = _make_workflow([("hello", 0.0)])
    item = make_item(item_id="1", title="T", body="B")
    preamble = wf._build_preamble(item)
    assert "### Comments" not in preamble


def test_preamble_includes_comments_when_present():
    wf = _make_workflow([("hello", 0.0)])
    item = _make_item_with_comments()
    preamble = wf._build_preamble(item)
    assert "### Comments" in preamble
    assert "alice" in preamble
    assert "Good idea" in preamble


def test_turn_prompt_first_turn_has_no_conversation_section():
    wf = _make_workflow([("hello", 0.0)])
    item = make_item(item_id="1", title="T", body="B")
    preamble = wf._build_preamble(item)
    prompt = wf._build_turn_prompt(preamble, [])
    assert "## Conversation so far" not in prompt


def test_turn_prompt_subsequent_turns_include_transcript():
    wf = _make_workflow([("hello", 0.0)])
    item = make_item(item_id="1", title="T", body="B")
    preamble = wf._build_preamble(item)
    transcript = [
        InterviewTurn(
            assistant_message="What approach?",
            user_message="Use option A",
            cost_usd=0.01,
            duration_seconds=1.0,
        )
    ]
    prompt = wf._build_turn_prompt(preamble, transcript)
    assert "## Conversation so far" in prompt
    assert "What approach?" in prompt
    assert "Use option A" in prompt


def test_turn_prompt_always_ends_with_sentinel_instruction():
    wf = _make_workflow([("hello", 0.0)])
    item = make_item(item_id="1", title="T", body="B")
    preamble = wf._build_preamble(item)
    prompt = wf._build_turn_prompt(preamble, [])
    assert _INTERVIEW_COMPLETE in prompt


# ---------------------------------------------------------------------------
# Loop — happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_interview_single_turn_sentinel(tmp_path):
    sentinel_msg = f"Great, I have what I need. {_INTERVIEW_COMPLETE}"
    wf = _make_workflow([(sentinel_msg, 0.05)])
    item = make_item(item_id="1", title="T", body="B")
    sink = RecordingEventSink()
    provider = ScriptedInputProvider([])  # should not be called
    ctx = _make_ctx(item=item, sink=sink, provider=provider, tmp_path=tmp_path)
    transcript = await wf._run_interview(ctx)
    assert len(transcript) == 1
    assert transcript[0].end == InterviewEnd.SENTINEL


@pytest.mark.asyncio
async def test_interview_multi_turn_sentinel(tmp_path):
    wf = _make_workflow(
        [
            ("First question?", 0.01),
            ("Second question?", 0.01),
            (f"All done. {_INTERVIEW_COMPLETE}", 0.01),
        ]
    )
    item = make_item(item_id="1", title="T", body="B")
    sink = RecordingEventSink()
    provider = ScriptedInputProvider(["answer1", "answer2"])
    ctx = _make_ctx(item=item, sink=sink, provider=provider, tmp_path=tmp_path)
    transcript = await wf._run_interview(ctx)
    assert len(transcript) == 3
    assert transcript[0].end == InterviewEnd.NOT_ENDED
    assert transcript[1].end == InterviewEnd.NOT_ENDED
    assert transcript[2].end == InterviewEnd.SENTINEL


@pytest.mark.asyncio
async def test_interview_sentinel_stripped_from_assistant_message(tmp_path):
    raw = f"Good to go! {_INTERVIEW_COMPLETE} Extra text after."
    wf = _make_workflow([(raw, 0.0)])
    item = make_item(item_id="1", title="T", body="B")
    sink = RecordingEventSink()
    provider = ScriptedInputProvider([])
    ctx = _make_ctx(item=item, sink=sink, provider=provider, tmp_path=tmp_path)
    transcript = await wf._run_interview(ctx)
    assert _INTERVIEW_COMPLETE not in transcript[0].assistant_message


# ---------------------------------------------------------------------------
# Loop — user early-end
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_interview_emits_stage_end_event_per_turn_with_cost(tmp_path):
    # Regression: the RunScreen footer's live cost counter consumes StageEndEvent.
    # Each interview turn must synthesize one so interview costs show up in the
    # footer — otherwise the user sees $0 throughout the interview.
    wf = _make_workflow(
        [
            ("First question?", 0.02),
            (f"All done. {_INTERVIEW_COMPLETE}", 0.03),
        ]
    )
    item = make_item(item_id="1", title="T", body="B")
    sink = RecordingEventSink()
    provider = ScriptedInputProvider(["an answer"])
    ctx = _make_ctx(item=item, sink=sink, provider=provider, tmp_path=tmp_path)
    await wf._run_interview(ctx)

    stage_ends = [e for e in sink.events if isinstance(e, StageEndEvent)]
    assert [e.cost_usd for e in stage_ends] == [0.02, 0.03]
    assert all(e.stage_name == "interview" for e in stage_ends)


@pytest.mark.asyncio
async def test_interview_user_ends_via_none_prompt(tmp_path):
    wf = _make_workflow([("Question?", 0.02)])
    item = make_item(item_id="1", title="T", body="B")
    sink = RecordingEventSink()
    provider = ScriptedInputProvider([None])
    ctx = _make_ctx(item=item, sink=sink, provider=provider, tmp_path=tmp_path)
    transcript = await wf._run_interview(ctx)
    assert len(transcript) == 1
    assert transcript[0].end == InterviewEnd.USER
    assert transcript[0].user_message is None


@pytest.mark.asyncio
async def test_interview_user_end_preserves_assistant_message_as_is(tmp_path):
    wf = _make_workflow([("What do you need?", 0.0)])
    item = make_item(item_id="1", title="T", body="B")
    sink = RecordingEventSink()
    provider = ScriptedInputProvider([None])
    ctx = _make_ctx(item=item, sink=sink, provider=provider, tmp_path=tmp_path)
    transcript = await wf._run_interview(ctx)
    assert transcript[0].assistant_message == "What do you need?"


# ---------------------------------------------------------------------------
# Loop — cost & duration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_interview_cost_sums_across_turns(tmp_path):
    wf = _make_workflow(
        [
            ("Question?", 0.10),
            (f"Done. {_INTERVIEW_COMPLETE}", 0.20),
        ]
    )
    item = make_item(item_id="1", title="T", body="B")
    sink = RecordingEventSink()
    provider = ScriptedInputProvider(["reply"])
    ctx = _make_ctx(item=item, sink=sink, provider=provider, tmp_path=tmp_path)
    transcript = await wf._run_interview(ctx)
    total = sum(t.cost_usd for t in transcript)
    assert abs(total - 0.30) < 1e-9


@pytest.mark.asyncio
async def test_interview_duration_sums_across_turns(tmp_path):
    wf = _make_workflow(
        [
            ("Q1?", 0.0),
            (f"Done. {_INTERVIEW_COMPLETE}", 0.0),
        ]
    )
    item = make_item(item_id="1", title="T", body="B")
    sink = RecordingEventSink()
    provider = ScriptedInputProvider(["reply"])
    ctx = _make_ctx(item=item, sink=sink, provider=provider, tmp_path=tmp_path)
    transcript = await wf._run_interview(ctx)
    total_duration = sum(t.duration_seconds for t in transcript)
    assert total_duration >= 0.0


# ---------------------------------------------------------------------------
# Loop — event forwarding
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_interview_forwards_assistant_events_to_sink(tmp_path):
    sentinel_msg = f"hello {_INTERVIEW_COMPLETE}"
    wf = _make_workflow([(sentinel_msg, 0.0)])
    item = make_item(item_id="1", title="T", body="B")
    sink = RecordingEventSink()
    provider = ScriptedInputProvider([])
    ctx = _make_ctx(item=item, sink=sink, provider=provider, tmp_path=tmp_path)
    await wf._run_interview(ctx)
    assert any(isinstance(e, AssistantTextEvent) for e in sink.events)


@pytest.mark.asyncio
async def test_interview_does_not_forward_result_events(tmp_path):
    sentinel_msg = f"done {_INTERVIEW_COMPLETE}"
    wf = _make_workflow([(sentinel_msg, 0.0)])
    item = make_item(item_id="1", title="T", body="B")
    sink = RecordingEventSink()
    provider = ScriptedInputProvider([])
    ctx = _make_ctx(item=item, sink=sink, provider=provider, tmp_path=tmp_path)
    await wf._run_interview(ctx)
    assert not any(isinstance(e, ResultEvent) for e in sink.events)


# ---------------------------------------------------------------------------
# Loop — failure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_interview_raises_when_event_sink_missing(tmp_path):
    wf = _make_workflow([("Q?", 0.0)])
    item = make_item(item_id="1", title="T", body="B")
    ctx = _make_ctx(item=item, sink=None, provider=ScriptedInputProvider([]), tmp_path=tmp_path)
    with pytest.raises(AssertionError, match="event sink"):
        await wf._run_interview(ctx)


@pytest.mark.asyncio
async def test_interview_raises_when_input_provider_missing(tmp_path):
    wf = _make_workflow([("Q?", 0.0)])
    item = make_item(item_id="1", title="T", body="B")
    ctx = _make_ctx(item=item, sink=RecordingEventSink(), provider=None, tmp_path=tmp_path)
    with pytest.raises(AssertionError, match="input provider"):
        await wf._run_interview(ctx)


@pytest.mark.asyncio
async def test_interview_runner_exception_propagates(tmp_path):
    from tests.fakes import FailingRunner

    wf = RefineWorkflow(runner=FailingRunner(RuntimeError("boom")), tracker=AsyncMock())
    item = make_item(item_id="1", title="T", body="B")
    sink = RecordingEventSink()
    provider = ScriptedInputProvider([])
    ctx = _make_ctx(item=item, sink=sink, provider=provider, tmp_path=tmp_path)
    with pytest.raises(RuntimeError, match="boom"):
        await wf._run_interview(ctx)


@pytest.mark.asyncio
async def test_interview_no_result_event_emitted_raises(tmp_path):
    """Runner that never emits ResultEvent should cause _run_interview to loop
    with empty final_message, not crash — but with no sentinel and no user input
    it would loop forever. Verify empty final_message with user-end works."""
    from collections.abc import AsyncIterator

    from cog.core.runner import AgentRunner, RunEvent

    class NoResultRunner(AgentRunner):
        async def stream(
            self, prompt: str, *, model: str, cwd: Path | None = None
        ) -> AsyncIterator[RunEvent]:
            # yields nothing (no ResultEvent → final_message stays "")
            return
            yield  # type: ignore[misc]

    wf = RefineWorkflow(runner=NoResultRunner(), tracker=AsyncMock())
    item = make_item(item_id="1", title="T", body="B")
    sink = RecordingEventSink()
    provider = ScriptedInputProvider([None])  # user ends on first turn
    ctx = _make_ctx(item=item, sink=sink, provider=provider, tmp_path=tmp_path)
    transcript = await wf._run_interview(ctx)
    assert transcript[0].end == InterviewEnd.USER
    assert transcript[0].assistant_message == ""


# ---------------------------------------------------------------------------
# pre_stages
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pre_stages_stores_transcript_by_item_id(tmp_path):
    sentinel_msg = f"All good. {_INTERVIEW_COMPLETE}"
    wf = _make_workflow([(sentinel_msg, 0.0)])
    item = make_item(item_id="42", title="T", body="B")
    sink = RecordingEventSink()
    provider = ScriptedInputProvider([])
    ctx = _make_ctx(item=item, sink=sink, provider=provider, tmp_path=tmp_path)
    await wf.pre_stages(ctx)
    assert "42" in wf._transcripts
    assert len(wf._transcripts["42"]) == 1


@pytest.mark.asyncio
async def test_pre_stages_raises_when_item_not_set(tmp_path):
    wf = _make_workflow([("Q?", 0.0)])
    ctx = _make_ctx(
        item=None,
        sink=RecordingEventSink(),
        provider=ScriptedInputProvider([]),
        tmp_path=tmp_path,
    )
    with pytest.raises(AssertionError, match="ctx.item"):
        await wf.pre_stages(ctx)


@pytest.mark.asyncio
async def test_pre_stages_propagates_interview_failure(tmp_path):
    from tests.fakes import FailingRunner

    wf = RefineWorkflow(runner=FailingRunner(ValueError("oops")), tracker=AsyncMock())
    item = make_item(item_id="1", title="T", body="B")
    ctx = _make_ctx(
        item=item,
        sink=RecordingEventSink(),
        provider=ScriptedInputProvider([]),
        tmp_path=tmp_path,
    )
    with pytest.raises(ValueError, match="oops"):
        await wf.pre_stages(ctx)


# ---------------------------------------------------------------------------
# Synthetic telemetry stage
# ---------------------------------------------------------------------------


def test_interview_telemetry_stage_shape():
    wf = _make_workflow([("Q?", 0.0)])
    transcript = [
        InterviewTurn(
            assistant_message="Q1?", user_message="A1", cost_usd=0.10, duration_seconds=2.0
        ),
        InterviewTurn(
            assistant_message="Done",
            user_message=None,
            cost_usd=0.05,
            duration_seconds=1.5,
            end=InterviewEnd.SENTINEL,
        ),
    ]
    stage = wf._interview_telemetry_stage(transcript, "claude-sonnet-4-6")
    assert stage.stage == "interview"
    assert stage.model == "claude-sonnet-4-6"
    assert abs(stage.duration_s - 3.5) < 1e-9
    assert abs(stage.cost_usd - 0.15) < 1e-9
    assert stage.exit_status == 0
    assert stage.commits == 0


def test_interview_telemetry_stage_empty_transcript_returns_zeros():
    wf = _make_workflow([("Q?", 0.0)])
    stage = wf._interview_telemetry_stage([], "claude-sonnet-4-6")
    assert stage.duration_s == 0.0
    assert stage.cost_usd == 0.0


# ---------------------------------------------------------------------------
# Minimal finalize_error
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_finalize_error_writes_stderr_warning_with_item_id(tmp_path, capsys):
    wf = _make_workflow([("Q?", 0.0)])
    item = make_item(item_id="7", title="T", body="B")
    ctx = _make_ctx(
        item=item,
        sink=RecordingEventSink(),
        provider=ScriptedInputProvider([]),
        tmp_path=tmp_path,
    )
    await wf.finalize_error(ctx, RuntimeError("bad"), [])
    captured = capsys.readouterr()
    assert "#7" in captured.err
    assert "RuntimeError" in captured.err
    assert "bad" in captured.err


@pytest.mark.asyncio
async def test_finalize_error_writes_partial_telemetry_when_transcript_exists(tmp_path):
    wf = _make_workflow([("Q?", 0.0)])
    item = make_item(item_id="8", title="T", body="B")
    # Pre-populate a transcript
    wf._transcripts["8"] = [
        InterviewTurn(assistant_message="Q?", user_message="A", cost_usd=0.05, duration_seconds=1.0)
    ]
    fake_writer = AsyncMock()
    ctx = ExecutionContext(
        project_dir=tmp_path,
        tmp_dir=tmp_path / "tmp",
        state_cache=InMemoryStateCache(),
        headless=False,
        item=item,
        telemetry=fake_writer,
    )
    await wf.finalize_error(ctx, RuntimeError("x"), [])
    fake_writer.write.assert_called_once()
    record = fake_writer.write.call_args[0][0]
    assert record.workflow == "refine"
    assert record.outcome == "error"
    # interview stage should be in stages tuple
    assert any(s.stage == "interview" for s in record.stages)


@pytest.mark.asyncio
async def test_finalize_error_skips_telemetry_when_transcript_empty(tmp_path):
    wf = _make_workflow([("Q?", 0.0)])
    item = make_item(item_id="9", title="T", body="B")
    fake_writer = AsyncMock()
    ctx = ExecutionContext(
        project_dir=tmp_path,
        tmp_dir=tmp_path / "tmp",
        state_cache=InMemoryStateCache(),
        headless=False,
        item=item,
        telemetry=fake_writer,
    )
    await wf.finalize_error(ctx, RuntimeError("x"), [])
    fake_writer.write.assert_not_called()


@pytest.mark.asyncio
async def test_finalize_error_tolerates_missing_telemetry_writer(tmp_path, capsys):
    wf = _make_workflow([("Q?", 0.0)])
    item = make_item(item_id="10", title="T", body="B")
    ctx = _make_ctx(item=item, sink=None, provider=None, tmp_path=tmp_path)
    # Should not raise even with no telemetry writer
    await wf.finalize_error(ctx, RuntimeError("x"), [])
    captured = capsys.readouterr()
    assert "#10" in captured.err
