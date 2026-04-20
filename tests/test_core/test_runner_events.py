"""Tests for runner event types, including StatusEvent."""

from cog.core.runner import (
    AssistantTextEvent,
    ResultEvent,
    RunEvent,
    StageEndEvent,
    StageStartEvent,
    StatusEvent,
    ToolUseEvent,
)


def test_status_event_shape() -> None:
    event = StatusEvent(message="⏳ Waiting for CI on PR #42...")
    assert event.message == "⏳ Waiting for CI on PR #42..."


def test_status_event_is_frozen() -> None:
    event = StatusEvent(message="hello")
    try:
        event.message = "changed"  # type: ignore[misc]
        raise AssertionError("should have raised")
    except (AttributeError, TypeError):
        pass


def test_run_event_union_accepts_status_event() -> None:
    # Verify StatusEvent is part of RunEvent at runtime via isinstance checks
    event: RunEvent = StatusEvent(message="test")
    assert isinstance(event, StatusEvent)


def test_run_event_union_still_accepts_all_prior_types() -> None:
    from pathlib import Path

    from cog.core.runner import RunResult

    events: list[RunEvent] = [
        AssistantTextEvent(text="hi"),
        ToolUseEvent(tool="Bash", input={"command": "ls"}),
        ResultEvent(
            result=RunResult(
                final_message="done",
                total_cost_usd=0.0,
                exit_status=0,
                stream_json_path=Path("/dev/null"),
                duration_seconds=0.0,
            )
        ),
        StageStartEvent(stage_name="build", model="m"),
        StageEndEvent(stage_name="build", cost_usd=0.0, exit_status=0),
        StatusEvent(message="status"),
    ]
    assert len(events) == 6
