"""Tests for StderrEventSink formatting."""

from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

import pytest

from cog.core.runner import (
    AssistantTextEvent,
    ResultEvent,
    RunResult,
    StageEndEvent,
    StageStartEvent,
    ToolUseEvent,
)
from cog.headless import StderrEventSink


@pytest.fixture
def sink():
    return StderrEventSink()


async def test_stage_start_event_format(sink, capsys):
    await sink.emit(StageStartEvent(stage_name="build", model="claude-sonnet-4-6"))
    captured = capsys.readouterr()
    assert "=== build (claude-sonnet-4-6) ===" in captured.err


async def test_stage_end_event_format(sink, capsys):
    await sink.emit(StageEndEvent(stage_name="build", cost_usd=0.087, exit_status=0))
    captured = capsys.readouterr()
    assert "=== build complete: $0.087 (exit 0) ===" in captured.err


async def test_tool_use_event_with_command(sink, capsys):
    await sink.emit(ToolUseEvent(tool="Bash", input={"command": "ls -la"}))
    captured = capsys.readouterr()
    assert "  > Bash: ls -la\n" in captured.err


async def test_tool_use_event_with_file_path(sink, capsys):
    await sink.emit(ToolUseEvent(tool="Read", input={"file_path": "/src/main.py"}))
    captured = capsys.readouterr()
    assert "  > Read: /src/main.py\n" in captured.err


async def test_tool_use_event_with_neither_shows_empty_preview(sink, capsys):
    await sink.emit(ToolUseEvent(tool="Grep", input={"pattern": "foo"}))
    captured = capsys.readouterr()
    assert "  > Grep: \n" in captured.err


async def test_assistant_text_event_indents_each_line(sink, capsys):
    await sink.emit(AssistantTextEvent(text="line one\nline two\nline three"))
    captured = capsys.readouterr()
    assert "  line one\n" in captured.err
    assert "  line two\n" in captured.err
    assert "  line three\n" in captured.err


async def test_result_event_emits_nothing(sink, capsys):
    result = RunResult(
        final_message="done",
        total_cost_usd=0.0,
        exit_status=0,
        stream_json_path=Path("/dev/null"),
        duration_seconds=0.0,
    )
    await sink.emit(ResultEvent(result=result))
    captured = capsys.readouterr()
    assert captured.err == ""


async def test_unknown_event_silently_dropped(sink, capsys):
    @dataclass(frozen=True)
    class FutureEvent:
        data: str

    # Type: ignore — intentionally passing a non-RunEvent to test forward-compat
    await sink.emit(FutureEvent(data="hello"))  # type: ignore[arg-type]
    captured = capsys.readouterr()
    assert captured.err == ""


async def test_flush_called_after_each_emit(sink):
    with patch("sys.stderr") as mock_stderr:
        mock_stderr.write = lambda s: None
        await sink.emit(StageStartEvent(stage_name="x", model="m"))
        mock_stderr.flush.assert_called()


async def test_stderr_sink_renders_status_event_to_stderr_with_dim_prefix(sink, capsys):
    from cog.core.runner import StatusEvent

    await sink.emit(StatusEvent(message="⏳ Waiting for CI on PR #42..."))
    captured = capsys.readouterr()
    assert "⏳ Waiting for CI on PR #42..." in captured.err
    assert captured.err.startswith("--")
