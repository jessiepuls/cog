"""Tests for the ClaudeCliRunner asyncio StreamReader line-limit fix.

Large stream-json events (e.g. tool_result from a big Read) can exceed asyncio's
default 64 KiB per-line limit and raise ValueError. These tests verify the limit
is raised to the configured value and that large lines parse successfully.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from cog.core.runner import ResultEvent, RunEvent
from cog.runners.claude_cli import ClaudeCliRunner, _parse_int_env
from tests.test_runners.helpers import RecordingSandbox

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _RealSubprocessSandbox(RecordingSandbox):
    """Sandbox that replaces the simulated claude argv with a python3 one-liner."""

    def __init__(self, python_snippet: str) -> None:
        super().__init__()
        self._snippet = python_snippet

    def wrap_argv(self, argv, cwd=None):  # type: ignore[override]
        return ["python3", "-c", self._snippet]


async def _collect(stream) -> list[RunEvent]:
    return [event async for event in stream]


def _result_line(cost: float = 0.0) -> str:
    return json.dumps({"type": "result", "total_cost_usd": cost, "exit_status": 0})


# ---------------------------------------------------------------------------
# Real-subprocess tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_runner_handles_single_line_above_64kib():
    # Emit one assistant message whose JSON encoding is ~100 KiB — well above
    # asyncio's 64 KiB default limit — then a valid result line. The runner
    # must parse both without raising ValueError.
    big_text = "x" * 100_000
    assistant_line = json.dumps(
        {
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": big_text}]},
        }
    )
    snippet = (
        f"import sys\nprint({assistant_line!r})\nprint({_result_line()!r})\nsys.stdout.flush()\n"
    )
    sandbox = _RealSubprocessSandbox(snippet)
    runner = ClaudeCliRunner(sandbox)

    events = await asyncio.wait_for(_collect(runner.stream("ignored", model="m")), timeout=10.0)

    assert any(isinstance(e, ResultEvent) for e in events)


@pytest.mark.asyncio
async def test_runner_completes_normally_with_small_lines():
    # Baseline regression guard: normal small-line output still works.
    snippet = f"import sys\nprint({_result_line(0.42)!r})\nsys.stdout.flush()\n"
    sandbox = _RealSubprocessSandbox(snippet)
    runner = ClaudeCliRunner(sandbox)

    events = await asyncio.wait_for(_collect(runner.stream("ignored", model="m")), timeout=10.0)

    results = [e for e in events if isinstance(e, ResultEvent)]
    assert len(results) == 1
    assert results[0].result.total_cost_usd == pytest.approx(0.42)


# ---------------------------------------------------------------------------
# Unit tests for _parse_int_env and the limit constant
# ---------------------------------------------------------------------------


def test_line_limit_defaults_to_16_mib_when_env_unset(monkeypatch):
    monkeypatch.delenv("COG_STREAM_LINE_LIMIT_BYTES", raising=False)
    assert _parse_int_env("COG_STREAM_LINE_LIMIT_BYTES", 16 * 1024 * 1024) == 16 * 1024 * 1024


def test_line_limit_honors_cog_stream_line_limit_bytes_env_override(monkeypatch):
    monkeypatch.setenv("COG_STREAM_LINE_LIMIT_BYTES", "131072")
    assert _parse_int_env("COG_STREAM_LINE_LIMIT_BYTES", 16 * 1024 * 1024) == 131072


def test_line_limit_invalid_env_value_warns_and_uses_default(monkeypatch, capsys):
    monkeypatch.setenv("COG_STREAM_LINE_LIMIT_BYTES", "not-a-number")
    result = _parse_int_env("COG_STREAM_LINE_LIMIT_BYTES", 16 * 1024 * 1024)
    assert result == 16 * 1024 * 1024
    captured = capsys.readouterr()
    assert "WARNING" in captured.err
    assert "COG_STREAM_LINE_LIMIT_BYTES" in captured.err
