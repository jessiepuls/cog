"""Unit tests for CommentaryCapture."""

from __future__ import annotations

import pytest

from cog.core.runner import (
    AssistantTextEvent,
    RunEvent,
    StageEndEvent,
    StageStartEvent,
    ToolUseEvent,
)
from cog.workflows.ralph import CommentaryCapture
from tests.fakes import RecordingEventSink

# ---------------------------------------------------------------------------
# render() with no turns
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_render_empty_returns_empty_string() -> None:
    cap = CommentaryCapture(inner=None)
    assert cap.render() == ""


@pytest.mark.asyncio
async def test_render_with_only_stage_events_but_no_text_returns_empty() -> None:
    cap = CommentaryCapture(inner=None)
    await cap.emit(StageStartEvent(stage_name="build", model="m"))
    await cap.emit(StageEndEvent(stage_name="build", cost_usd=0.0, exit_status=0))
    assert cap.render() == ""


# ---------------------------------------------------------------------------
# Basic stage grouping
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_two_stages_rendered_in_order() -> None:
    cap = CommentaryCapture(inner=None)
    await cap.emit(StageStartEvent(stage_name="build", model="m"))
    await cap.emit(AssistantTextEvent(text="build note"))
    await cap.emit(StageEndEvent(stage_name="build", cost_usd=0.0, exit_status=0))
    await cap.emit(StageStartEvent(stage_name="review", model="m"))
    await cap.emit(AssistantTextEvent(text="review note"))
    await cap.emit(StageEndEvent(stage_name="review", cost_usd=0.0, exit_status=0))

    rendered = cap.render()
    build_pos = rendered.index("### build")
    review_pos = rendered.index("### review")
    assert build_pos < review_pos
    assert "build note" in rendered
    assert "review note" in rendered


@pytest.mark.asyncio
async def test_multiple_turns_within_stage_separated_by_blank_line() -> None:
    cap = CommentaryCapture(inner=None)
    await cap.emit(StageStartEvent(stage_name="build", model="m"))
    await cap.emit(AssistantTextEvent(text="first"))
    await cap.emit(AssistantTextEvent(text="second"))
    await cap.emit(StageEndEvent(stage_name="build", cost_usd=0.0, exit_status=0))

    rendered = cap.render()
    assert "first\n\nsecond" in rendered


# ---------------------------------------------------------------------------
# Drop rules
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("text", ["", "   ", "\n\n", "\t  "])
@pytest.mark.asyncio
async def test_whitespace_only_text_is_dropped(text: str) -> None:
    cap = CommentaryCapture(inner=None)
    await cap.emit(StageStartEvent(stage_name="build", model="m"))
    await cap.emit(AssistantTextEvent(text=text))
    await cap.emit(StageEndEvent(stage_name="build", cost_usd=0.0, exit_status=0))

    assert cap.render() == ""


@pytest.mark.asyncio
async def test_text_before_first_stage_start_is_dropped() -> None:
    cap = CommentaryCapture(inner=None)
    await cap.emit(AssistantTextEvent(text="pre-stage text"))
    await cap.emit(StageStartEvent(stage_name="build", model="m"))
    await cap.emit(AssistantTextEvent(text="in-stage text"))
    await cap.emit(StageEndEvent(stage_name="build", cost_usd=0.0, exit_status=0))

    rendered = cap.render()
    assert "pre-stage text" not in rendered
    assert "in-stage text" in rendered


@pytest.mark.asyncio
async def test_text_between_stages_is_dropped() -> None:
    cap = CommentaryCapture(inner=None)
    await cap.emit(StageStartEvent(stage_name="build", model="m"))
    await cap.emit(AssistantTextEvent(text="build text"))
    await cap.emit(StageEndEvent(stage_name="build", cost_usd=0.0, exit_status=0))
    await cap.emit(AssistantTextEvent(text="between stages"))
    await cap.emit(StageStartEvent(stage_name="review", model="m"))
    await cap.emit(AssistantTextEvent(text="review text"))
    await cap.emit(StageEndEvent(stage_name="review", cost_usd=0.0, exit_status=0))

    rendered = cap.render()
    assert "between stages" not in rendered
    assert "build text" in rendered
    assert "review text" in rendered


@pytest.mark.asyncio
async def test_text_after_final_stage_end_is_dropped() -> None:
    cap = CommentaryCapture(inner=None)
    await cap.emit(StageStartEvent(stage_name="build", model="m"))
    await cap.emit(AssistantTextEvent(text="in-stage text"))
    await cap.emit(StageEndEvent(stage_name="build", cost_usd=0.0, exit_status=0))
    await cap.emit(AssistantTextEvent(text="post-stage text"))

    rendered = cap.render()
    assert "post-stage text" not in rendered
    assert "in-stage text" in rendered


@pytest.mark.asyncio
async def test_stage_with_only_whitespace_turns_omits_heading() -> None:
    cap = CommentaryCapture(inner=None)
    await cap.emit(StageStartEvent(stage_name="build", model="m"))
    await cap.emit(AssistantTextEvent(text="  "))
    await cap.emit(StageEndEvent(stage_name="build", cost_usd=0.0, exit_status=0))

    assert "### build" not in cap.render()


# ---------------------------------------------------------------------------
# Tool events pass-through but not buffered
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_use_event_forwarded_not_buffered() -> None:
    inner = RecordingEventSink()
    cap = CommentaryCapture(inner=inner)
    await cap.emit(StageStartEvent(stage_name="build", model="m"))
    tool_event = ToolUseEvent(tool="Read", input={"file_path": "/foo"})
    await cap.emit(tool_event)
    await cap.emit(StageEndEvent(stage_name="build", cost_usd=0.0, exit_status=0))

    assert tool_event in inner.events
    assert cap.render() == ""


# ---------------------------------------------------------------------------
# Inner sink receives all events
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("use_inner", [True, False])
async def test_inner_sink_receives_all_events(use_inner: bool) -> None:
    inner = RecordingEventSink() if use_inner else None
    cap = CommentaryCapture(inner=inner)

    events: list[RunEvent] = [
        StageStartEvent(stage_name="build", model="m"),
        AssistantTextEvent(text="hello"),
        ToolUseEvent(tool="Read", input={}),
        StageEndEvent(stage_name="build", cost_usd=0.0, exit_status=0),
    ]
    for e in events:
        await cap.emit(e)

    if use_inner:
        assert inner is not None
        assert inner.events == events


# ---------------------------------------------------------------------------
# Heading demotion
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "input_text,expected_fragment",
    [
        ("# Title", "## Title"),
        ("## Section", "### Section"),
        ("### Sub", "#### Sub"),
        ("see #42 and #100", "see #42 and #100"),  # inline # untouched
        ("no heading here", "no heading here"),
        ("```\n# code comment\n```", "```\n## code comment\n```"),  # line-anchored, acceptable
    ],
)
async def test_heading_demotion(input_text: str, expected_fragment: str) -> None:
    cap = CommentaryCapture(inner=None)
    await cap.emit(StageStartEvent(stage_name="build", model="m"))
    await cap.emit(AssistantTextEvent(text=input_text))
    await cap.emit(StageEndEvent(stage_name="build", cost_usd=0.0, exit_status=0))

    assert expected_fragment in cap.render()


@pytest.mark.asyncio
async def test_code_fence_bullets_blockquotes_pass_through() -> None:
    text = "- item\n> quote\n```python\nprint('hi')\n```"
    cap = CommentaryCapture(inner=None)
    await cap.emit(StageStartEvent(stage_name="build", model="m"))
    await cap.emit(AssistantTextEvent(text=text))
    await cap.emit(StageEndEvent(stage_name="build", cost_usd=0.0, exit_status=0))

    rendered = cap.render()
    assert "- item" in rendered
    assert "> quote" in rendered
    assert "```python" in rendered


# ---------------------------------------------------------------------------
# Intra-turn line breaks preserved
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_intra_turn_line_breaks_preserved() -> None:
    cap = CommentaryCapture(inner=None)
    await cap.emit(StageStartEvent(stage_name="build", model="m"))
    await cap.emit(AssistantTextEvent(text="line one\nline two\nline three"))
    await cap.emit(StageEndEvent(stage_name="build", cost_usd=0.0, exit_status=0))

    rendered = cap.render()
    assert "line one\nline two\nline three" in rendered
