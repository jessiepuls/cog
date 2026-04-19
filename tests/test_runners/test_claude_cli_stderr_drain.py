"""Regression tests for the concurrent stderr drain in ClaudeCliRunner.

Without a drain, a subprocess that writes more than ~64KB to stderr fills
the OS pipe buffer, blocks on the next write, and stalls stdout — which
manifests as RunnerStalledError (#48). These tests use a real subprocess
(python3 -c) because the pipe-fill dynamics can't be simulated with mocks.
"""

from __future__ import annotations

import asyncio

import pytest

from cog.core.runner import ResultEvent, RunEvent
from cog.runners.claude_cli import ClaudeCliRunner
from tests.test_runners.helpers import RecordingSandbox


class _RealSubprocessSandbox(RecordingSandbox):
    """Sandbox that replaces `claude ...` with a python one-liner inline so we
    hit asyncio's real subprocess machinery."""

    def __init__(self, python_snippet: str) -> None:
        super().__init__()
        self._snippet = python_snippet

    def wrap_argv(self, argv):  # type: ignore[override]
        # Ignore the simulated claude argv entirely; invoke python3 -c <snippet>.
        return ["python3", "-c", self._snippet]


async def _collect(stream) -> list[RunEvent]:
    return [event async for event in stream]


@pytest.mark.asyncio
async def test_runner_does_not_hang_on_large_stderr_output():
    # Script that writes 200KB to stderr (well above typical 64KB pipe
    # buffer), then a minimal valid stream-json result line, then exits.
    # Without stderr drain, the stderr write blocks at ~64KB and the runner
    # trips its 120s inactivity timeout instead of completing promptly.
    snippet = (
        "import sys, json\n"
        "sys.stderr.write('x' * 200_000)\n"
        "sys.stderr.flush()\n"
        "print(json.dumps({'type': 'result', 'total_cost_usd': 0.0, 'exit_status': 0}))\n"
        "sys.stdout.flush()\n"
    )
    sandbox = _RealSubprocessSandbox(snippet)
    runner = ClaudeCliRunner(sandbox)

    # Bound the test: if the drain regresses and the process blocks, this
    # wait_for fires long before the runner's own 120s inactivity timer.
    events = await asyncio.wait_for(_collect(runner.stream("ignored", model="m")), timeout=10.0)

    assert any(isinstance(e, ResultEvent) for e in events)


@pytest.mark.asyncio
async def test_runner_completes_normally_when_stderr_is_small():
    # Baseline: small stderr, normal flow. Guards against regressions where
    # the drain accidentally blocks completion (e.g., infinite await).
    snippet = (
        "import sys, json\n"
        "sys.stderr.write('small stderr')\n"
        "sys.stderr.flush()\n"
        "print(json.dumps({'type': 'result', 'total_cost_usd': 0.01, 'exit_status': 0}))\n"
        "sys.stdout.flush()\n"
    )
    sandbox = _RealSubprocessSandbox(snippet)
    runner = ClaudeCliRunner(sandbox)

    events = await asyncio.wait_for(_collect(runner.stream("ignored", model="m")), timeout=10.0)

    results = [e for e in events if isinstance(e, ResultEvent)]
    assert len(results) == 1
    assert results[0].result.total_cost_usd == 0.01


@pytest.mark.asyncio
async def test_drain_tolerates_fake_proc_without_stderr():
    # Regression guard for the `proc.stderr is not None` check: the existing
    # FakeProcess helpers leave stderr=None, and the drain must not AttributeError.
    from tests.test_runners.helpers import fixture_proc, patch_exec

    proc = fixture_proc("happy.jsonl")
    sandbox = RecordingSandbox()
    runner = ClaudeCliRunner(sandbox)

    with patch_exec(proc):
        events = await _collect(runner.stream("ignored", model="m"))

    assert any(isinstance(e, ResultEvent) for e in events)
