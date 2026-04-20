"""Tests for ClaudeCliRunner stream-inactivity timeout behavior."""

import json

import pytest

from cog.core.errors import RunnerError, RunnerStalledError, RunnerTimeoutError
from cog.runners.claude_cli import ClaudeCliRunner
from cog.runners.sandbox import NullSandbox
from tests.test_runners.helpers import PausingMockProc, StreamEvent, patch_pausing_exec

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ASSISTANT_TEXT = (
    json.dumps(
        {
            "type": "assistant",
            "message": {
                "content": [{"type": "text", "text": "I will help you."}],
            },
        }
    ).encode()
    + b"\n"
)

_TOOL_USE = (
    json.dumps(
        {
            "type": "assistant",
            "message": {
                "content": [{"type": "tool_use", "name": "Bash", "input": {"command": "echo hi"}}],
            },
        }
    ).encode()
    + b"\n"
)

_READ_TOOL_USE = (
    json.dumps(
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "name": "Read",
                        "input": {"file_path": "/tmp/some/path.txt"},
                    }
                ],
            },
        }
    ).encode()
    + b"\n"
)

_RESULT = (
    json.dumps(
        {
            "type": "result",
            "exit_status": 0,
            "total_cost_usd": 0.01,
        }
    ).encode()
    + b"\n"
)


def _runner(*, inactivity: float = 0.05, wall_clock: float = 30.0) -> ClaudeCliRunner:
    r = ClaudeCliRunner(NullSandbox())
    r._inactivity_timeout_seconds = inactivity
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
# Core behavior
# ---------------------------------------------------------------------------


async def test_inactivity_timeout_fires_when_no_events():
    proc = PausingMockProc(
        [
            StreamEvent(_ASSISTANT_TEXT),
            StreamEvent(_ASSISTANT_TEXT),
            StreamEvent(_ASSISTANT_TEXT),
            StreamEvent(b"", delay_before=9999),  # hang forever
        ]
    )
    runner = _runner()
    with pytest.raises(RunnerStalledError):
        await _drain(runner, proc)


async def test_inactivity_timeout_does_not_fire_for_fast_stream():
    proc = PausingMockProc(
        [
            StreamEvent(_ASSISTANT_TEXT),
            StreamEvent(_RESULT),
            StreamEvent(b""),  # EOF
        ]
    )
    runner = _runner(inactivity=5.0)
    events = await _drain(runner, proc)
    assert len(events) >= 1


async def test_inactivity_timeout_does_not_fire_for_healthy_stream_with_pauses():
    # Each event has a small delay well under the inactivity timeout.
    proc = PausingMockProc(
        [
            StreamEvent(_ASSISTANT_TEXT, delay_before=0.01),
            StreamEvent(_ASSISTANT_TEXT, delay_before=0.01),
            StreamEvent(_RESULT, delay_before=0.01),
            StreamEvent(b""),
        ]
    )
    runner = _runner(inactivity=5.0)
    events = await _drain(runner, proc)
    assert len(events) >= 1


# ---------------------------------------------------------------------------
# Error population
# ---------------------------------------------------------------------------


async def test_stalled_error_includes_inactivity_seconds():
    proc = PausingMockProc([StreamEvent(b"", delay_before=9999)])
    runner = _runner(inactivity=0.07)
    with pytest.raises(RunnerStalledError) as exc_info:
        await _drain(runner, proc)
    assert exc_info.value.inactivity_seconds == pytest.approx(0.07)


async def test_stalled_error_last_event_summary_for_tool_use():
    proc = PausingMockProc(
        [
            StreamEvent(_TOOL_USE),
            StreamEvent(b"", delay_before=9999),
        ]
    )
    runner = _runner()
    with pytest.raises(RunnerStalledError) as exc_info:
        await _drain(runner, proc)
    summary = exc_info.value.last_event_summary
    assert summary is not None
    assert "Bash" in summary
    assert "echo hi" in summary


async def test_stalled_error_last_event_summary_for_assistant_text():
    proc = PausingMockProc(
        [
            StreamEvent(_ASSISTANT_TEXT),
            StreamEvent(b"", delay_before=9999),
        ]
    )
    runner = _runner()
    with pytest.raises(RunnerStalledError) as exc_info:
        await _drain(runner, proc)
    summary = exc_info.value.last_event_summary
    assert summary is not None
    assert "assistant:" in summary
    assert "I will help you." in summary


async def test_stalled_error_last_event_summary_none_when_hang_before_any_event():
    proc = PausingMockProc([StreamEvent(b"", delay_before=9999)])
    runner = _runner()
    with pytest.raises(RunnerStalledError) as exc_info:
        await _drain(runner, proc)
    assert exc_info.value.last_event_summary is None


async def test_stalled_error_message_includes_last_event():
    proc = PausingMockProc(
        [
            StreamEvent(_ASSISTANT_TEXT),
            StreamEvent(b"", delay_before=9999),
        ]
    )
    runner = _runner()
    with pytest.raises(RunnerStalledError) as exc_info:
        await _drain(runner, proc)
    assert "I will help you." in str(exc_info.value)


async def test_stalled_error_message_shows_none_when_no_prior_event():
    proc = PausingMockProc([StreamEvent(b"", delay_before=9999)])
    runner = _runner()
    with pytest.raises(RunnerStalledError) as exc_info:
        await _drain(runner, proc)
    assert "(none)" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Process termination
# ---------------------------------------------------------------------------


async def test_inactivity_sigterm_sent_on_stall():
    proc = PausingMockProc([StreamEvent(b"", delay_before=9999)])
    runner = _runner()
    with pytest.raises(RunnerStalledError):
        await _drain(runner, proc)
    assert proc._terminated


async def test_inactivity_sigkill_after_grace_when_sigterm_ignored():
    proc = PausingMockProc([StreamEvent(b"", delay_before=9999)], respects_sigterm=False)
    runner = _runner()
    with pytest.raises(RunnerStalledError):
        await _drain(runner, proc)
    assert proc._killed


async def test_inactivity_proc_waited_before_raise():
    """No zombie: returncode is set before RunnerStalledError propagates."""
    proc = PausingMockProc([StreamEvent(b"", delay_before=9999)])
    runner = _runner()
    with pytest.raises(RunnerStalledError):
        await _drain(runner, proc)
    assert proc.returncode is not None


# ---------------------------------------------------------------------------
# Env var configuration
# ---------------------------------------------------------------------------


async def test_inactivity_default_is_300_seconds(monkeypatch):
    monkeypatch.delenv("COG_RUNNER_INACTIVITY_TIMEOUT_SECONDS", raising=False)
    runner = ClaudeCliRunner(NullSandbox())
    assert runner._inactivity_timeout_seconds == 300.0


async def test_inactivity_env_var_override(monkeypatch):
    monkeypatch.setenv("COG_RUNNER_INACTIVITY_TIMEOUT_SECONDS", "60")
    runner = ClaudeCliRunner(NullSandbox())
    assert runner._inactivity_timeout_seconds == 60.0


async def test_inactivity_env_var_invalid_falls_back_to_default(monkeypatch, capsys):
    monkeypatch.setenv("COG_RUNNER_INACTIVITY_TIMEOUT_SECONDS", "not-a-number")
    runner = ClaudeCliRunner(NullSandbox())
    assert runner._inactivity_timeout_seconds == 300.0
    captured = capsys.readouterr()
    assert "WARNING" in captured.err


# ---------------------------------------------------------------------------
# Interaction with wall-clock timeout
# ---------------------------------------------------------------------------


async def test_wall_clock_fires_independently_when_stream_runs_too_long():
    # Stream flows continuously but wall-clock is very short.
    proc = PausingMockProc([StreamEvent(b"", delay_before=9999)])
    runner = _runner(inactivity=0.5, wall_clock=0.05)
    with pytest.raises(RunnerTimeoutError):
        await _drain(runner, proc)


async def test_inactivity_fires_when_smaller_than_wall_clock():
    proc = PausingMockProc([StreamEvent(b"", delay_before=9999)])
    runner = _runner(inactivity=0.05, wall_clock=30.0)
    with pytest.raises(RunnerStalledError):
        await _drain(runner, proc)


async def test_both_error_classes_subclass_runner_error():
    assert issubclass(RunnerStalledError, RunnerError)
    assert issubclass(RunnerTimeoutError, RunnerError)


# ---------------------------------------------------------------------------
# last_event_summary details
# ---------------------------------------------------------------------------


async def test_last_event_summary_tool_use_uses_file_path_when_no_command():
    proc = PausingMockProc(
        [
            StreamEvent(_READ_TOOL_USE),
            StreamEvent(b"", delay_before=9999),
        ]
    )
    runner = _runner()
    with pytest.raises(RunnerStalledError) as exc_info:
        await _drain(runner, proc)
    summary = exc_info.value.last_event_summary
    assert summary is not None
    assert "Read" in summary
    assert "/tmp/some/path.txt" in summary
