"""Tests for ClaudeCliRunner two-tier tool-aware stall detection."""

import json

import pytest

from cog.core.errors import RunnerStalledError
from cog.runners.claude_cli import ClaudeCliRunner
from cog.runners.sandbox import NullSandbox
from tests.test_runners.helpers import PausingMockProc, StreamEvent, patch_pausing_exec

# ---------------------------------------------------------------------------
# JSONL fixtures
# ---------------------------------------------------------------------------


def _tool_use_line(tool_id: str, name: str, command: str) -> bytes:
    return (
        json.dumps(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": tool_id,
                            "name": name,
                            "input": {"command": command},
                        }
                    ]
                },
            }
        ).encode()
        + b"\n"
    )


def _tool_result_line(tool_use_id: str) -> bytes:
    return (
        json.dumps(
            {
                "type": "user",
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": tool_use_id,
                            "content": "ok",
                        }
                    ]
                },
            }
        ).encode()
        + b"\n"
    )


def _multi_tool_use_line(tools: list[tuple[str, str, str]]) -> bytes:
    """Single assistant record containing multiple tool_use blocks."""
    content = [
        {"type": "tool_use", "id": tid, "name": name, "input": {"command": cmd}}
        for tid, name, cmd in tools
    ]
    return json.dumps({"type": "assistant", "message": {"content": content}}).encode() + b"\n"


_RESULT = json.dumps({"type": "result", "exit_status": 0, "total_cost_usd": 0.01}).encode() + b"\n"

_ASSISTANT_TEXT = (
    json.dumps(
        {
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": "thinking..."}]},
        }
    ).encode()
    + b"\n"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _runner(
    *,
    inactivity: float = 0.05,
    tool_call: float = 0.3,
    wall_clock: float = 30.0,
) -> ClaudeCliRunner:
    r = ClaudeCliRunner(NullSandbox())
    r._inactivity_timeout_seconds = inactivity
    r._tool_call_timeout_seconds = tool_call
    r._timeout_seconds = wall_clock
    r._sigterm_grace_seconds = 0.05
    return r


async def _drain(runner: ClaudeCliRunner, proc: PausingMockProc) -> list:
    events = []
    with patch_pausing_exec(proc):
        async for ev in runner.stream("hello", model="m"):
            events.append(ev)
    return events


# ---------------------------------------------------------------------------
# Core behavior: timeout tier selection
# ---------------------------------------------------------------------------


async def test_long_bash_tool_call_does_not_fire_stall_before_tool_call_timeout():
    """A tool in progress uses the tool-call timeout, not idle timeout."""
    proc = PausingMockProc(
        [
            StreamEvent(_tool_use_line("id1", "Bash", "sleep 1")),
            # Delay shorter than tool_call timeout but longer than idle timeout.
            StreamEvent(_tool_result_line("id1"), delay_before=0.1),
            StreamEvent(_RESULT),
            StreamEvent(b""),
        ]
    )
    runner = _runner(inactivity=0.05, tool_call=5.0)
    # Should complete without stall.
    events = await _drain(runner, proc)
    assert events  # at least a ResultEvent


async def test_long_bash_tool_call_fires_stall_after_tool_call_timeout():
    """Hang during tool call eventually fires with tool-call timeout value."""
    proc = PausingMockProc(
        [
            StreamEvent(_tool_use_line("id1", "Bash", "uv run pytest")),
            StreamEvent(b"", delay_before=9999),  # hang forever
        ]
    )
    runner = _runner(inactivity=0.05, tool_call=0.1)
    with pytest.raises(RunnerStalledError) as exc_info:
        await _drain(runner, proc)
    assert exc_info.value.inactivity_seconds == pytest.approx(0.1)


async def test_idle_without_outstanding_tools_fires_at_idle_timeout():
    """No outstanding tool → idle timeout applies."""
    proc = PausingMockProc(
        [
            StreamEvent(_ASSISTANT_TEXT),
            StreamEvent(b"", delay_before=9999),
        ]
    )
    runner = _runner(inactivity=0.05, tool_call=10.0)
    with pytest.raises(RunnerStalledError) as exc_info:
        await _drain(runner, proc)
    assert exc_info.value.inactivity_seconds == pytest.approx(0.05)


async def test_multiple_concurrent_tools_uses_tool_call_timeout_while_any_outstanding():
    """With two outstanding tools, draining one still uses tool-call timeout."""
    proc = PausingMockProc(
        [
            StreamEvent(_multi_tool_use_line([("id1", "Bash", "cmd1"), ("id2", "Bash", "cmd2")])),
            StreamEvent(_tool_result_line("id1")),  # drain first, second still outstanding
            StreamEvent(b"", delay_before=9999),
        ]
    )
    runner = _runner(inactivity=0.05, tool_call=0.1)
    with pytest.raises(RunnerStalledError) as exc_info:
        await _drain(runner, proc)
    assert exc_info.value.inactivity_seconds == pytest.approx(0.1)


async def test_all_tools_drained_reverts_to_idle_timeout():
    """After all tool_results arrive, timeout reverts to idle."""
    proc = PausingMockProc(
        [
            StreamEvent(_tool_use_line("id1", "Bash", "cmd")),
            StreamEvent(_tool_result_line("id1")),  # drains outstanding set
            StreamEvent(b"", delay_before=9999),
        ]
    )
    runner = _runner(inactivity=0.05, tool_call=10.0)
    with pytest.raises(RunnerStalledError) as exc_info:
        await _drain(runner, proc)
    assert exc_info.value.inactivity_seconds == pytest.approx(0.05)


# ---------------------------------------------------------------------------
# Stall-context diagnostics
# ---------------------------------------------------------------------------


async def test_stall_during_tool_call_records_in_progress_tool_name():
    proc = PausingMockProc(
        [
            StreamEvent(_tool_use_line("id1", "Bash", "uv run pytest tests/")),
            StreamEvent(b"", delay_before=9999),
        ]
    )
    runner = _runner(inactivity=0.05, tool_call=0.1)
    with pytest.raises(RunnerStalledError) as exc_info:
        await _drain(runner, proc)
    assert exc_info.value.last_event_summary is not None
    assert "in-progress" in exc_info.value.last_event_summary
    assert "Bash" in exc_info.value.last_event_summary


async def test_stall_during_tool_call_records_tool_input_preview():
    proc = PausingMockProc(
        [
            StreamEvent(_tool_use_line("id1", "Bash", "uv run pytest tests/")),
            StreamEvent(b"", delay_before=9999),
        ]
    )
    runner = _runner(inactivity=0.05, tool_call=0.1)
    with pytest.raises(RunnerStalledError) as exc_info:
        await _drain(runner, proc)
    assert "uv run pytest" in exc_info.value.last_event_summary


async def test_stall_while_idle_records_last_completed_tool_name():
    proc = PausingMockProc(
        [
            StreamEvent(_tool_use_line("id1", "Bash", "echo hi")),
            StreamEvent(_tool_result_line("id1")),
            StreamEvent(b"", delay_before=9999),
        ]
    )
    runner = _runner(inactivity=0.05, tool_call=10.0)
    with pytest.raises(RunnerStalledError) as exc_info:
        await _drain(runner, proc)
    assert exc_info.value.last_event_summary is not None
    assert "completed" in exc_info.value.last_event_summary
    assert "Bash" in exc_info.value.last_event_summary


# ---------------------------------------------------------------------------
# Env var handling
# ---------------------------------------------------------------------------


async def test_cog_runner_tool_call_timeout_seconds_default_is_600(monkeypatch):
    monkeypatch.delenv("COG_RUNNER_TOOL_CALL_TIMEOUT_SECONDS", raising=False)
    runner = ClaudeCliRunner(NullSandbox())
    assert runner._tool_call_timeout_seconds == 600.0


async def test_cog_runner_tool_call_timeout_seconds_env_override(monkeypatch):
    monkeypatch.setenv("COG_RUNNER_TOOL_CALL_TIMEOUT_SECONDS", "300")
    runner = ClaudeCliRunner(NullSandbox())
    assert runner._tool_call_timeout_seconds == 300.0


async def test_cog_runner_inactivity_timeout_seconds_still_honored_for_idle_path(monkeypatch):
    monkeypatch.setenv("COG_RUNNER_INACTIVITY_TIMEOUT_SECONDS", "77")
    runner = ClaudeCliRunner(NullSandbox())
    assert runner._inactivity_timeout_seconds == 77.0

    # Verify it's actually used for the idle path (no outstanding tools).
    proc = PausingMockProc([StreamEvent(b"", delay_before=9999)])
    runner._inactivity_timeout_seconds = 0.05  # shorten for fast test
    runner._sigterm_grace_seconds = 0.05
    runner._timeout_seconds = 30.0
    with patch_pausing_exec(proc):
        with pytest.raises(RunnerStalledError) as exc_info:
            async for _ in runner.stream("hi", model="m"):
                pass
    assert exc_info.value.inactivity_seconds == pytest.approx(0.05)


# ---------------------------------------------------------------------------
# Tool lifecycle parsing (tested via timeout-tier behavior)
# ---------------------------------------------------------------------------


async def test_tool_use_populates_outstanding_set():
    """tool_use with id → timeout switches to tool_call tier."""
    proc = PausingMockProc(
        [
            StreamEvent(_tool_use_line("toolu_01", "Bash", "cmd")),
            StreamEvent(b"", delay_before=9999),
        ]
    )
    runner = _runner(inactivity=0.05, tool_call=0.15)
    with pytest.raises(RunnerStalledError) as exc_info:
        await _drain(runner, proc)
    # Stall fired at tool_call timeout, not idle timeout.
    assert exc_info.value.inactivity_seconds == pytest.approx(0.15)


async def test_tool_result_drains_outstanding_set_by_tool_use_id():
    """tool_result with matching tool_use_id → set drained → idle tier restored."""
    proc = PausingMockProc(
        [
            StreamEvent(_tool_use_line("toolu_01", "Bash", "cmd")),
            StreamEvent(_tool_result_line("toolu_01")),
            StreamEvent(b"", delay_before=9999),
        ]
    )
    runner = _runner(inactivity=0.05, tool_call=10.0)
    with pytest.raises(RunnerStalledError) as exc_info:
        await _drain(runner, proc)
    # Stall fired at idle timeout after drain.
    assert exc_info.value.inactivity_seconds == pytest.approx(0.05)


async def test_unknown_tool_result_without_matching_use_id_is_silently_ignored():
    """tool_result with no matching tool_use_id does not crash and does not affect the set."""
    proc = PausingMockProc(
        [
            StreamEvent(_tool_result_line("nonexistent_id")),
            StreamEvent(b"", delay_before=9999),
        ]
    )
    runner = _runner(inactivity=0.05, tool_call=10.0)
    # Should not crash; idle timeout fires since outstanding set is empty.
    with pytest.raises(RunnerStalledError) as exc_info:
        await _drain(runner, proc)
    assert exc_info.value.inactivity_seconds == pytest.approx(0.05)


async def test_tool_use_with_empty_id_is_not_tracked():
    """tool_use with empty/missing id is silently ignored and does not populate the set."""
    empty_id_line = (
        json.dumps(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "",
                            "name": "Bash",
                            "input": {"command": "cmd"},
                        }
                    ]
                },
            }
        ).encode()
        + b"\n"
    )
    proc = PausingMockProc(
        [
            StreamEvent(empty_id_line),
            StreamEvent(b"", delay_before=9999),
        ]
    )
    runner = _runner(inactivity=0.05, tool_call=10.0)
    # Empty id → not tracked → idle timeout applies, no crash.
    with pytest.raises(RunnerStalledError) as exc_info:
        await _drain(runner, proc)
    assert exc_info.value.inactivity_seconds == pytest.approx(0.05)
