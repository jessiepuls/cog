"""Tests for RalphWorkflow CI-wait loop and finalize CI-gate behavior."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from cog.core.context import ExecutionContext
from cog.core.host import CheckRun, GitHost, PrChecks, PullRequest
from cog.core.runner import StatusEvent
from cog.core.tracker import IssueTracker
from cog.workflows.ralph import RalphWorkflow
from tests.fakes import InMemoryStateCache, RecordingEventSink, make_item, make_stage_result

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _make_pr(number: int = 42, url: str = "https://github.com/org/repo/pull/42") -> PullRequest:
    return PullRequest(number=number, url=url, state="open", body="", head_branch="cog/42-fix")


def _passed_checks(*names: str) -> PrChecks:
    runs = tuple(CheckRun(name=n, state="passed", link=f"https://ci.example/{n}") for n in names)
    return PrChecks(runs=runs)


def _pending_checks(*names: str) -> PrChecks:
    runs = tuple(CheckRun(name=n, state="pending", link="") for n in names)
    return PrChecks(runs=runs)


def _failed_checks(*names: str) -> PrChecks:
    runs = tuple(CheckRun(name=n, state="failed", link=f"https://ci.example/{n}") for n in names)
    return PrChecks(runs=runs)


def _make_host(
    *,
    pr: PullRequest | None = None,
    push_error: Exception | None = None,
    checks_sequence: list[PrChecks] | None = None,
) -> AsyncMock:
    host = AsyncMock(spec=GitHost)
    if push_error:
        host.push_branch.side_effect = push_error
    else:
        host.push_branch.return_value = None
    host.get_pr_for_branch.return_value = pr
    host.create_pr.return_value = _make_pr()
    host.update_pr.return_value = None
    host.comment_on_pr.return_value = None
    if checks_sequence:
        host.get_pr_checks.side_effect = checks_sequence
    else:
        host.get_pr_checks.return_value = _passed_checks("ci")
    return host


def _make_tracker() -> AsyncMock:
    tracker = AsyncMock(spec=IssueTracker)
    tracker.comment.return_value = None
    tracker.add_label.return_value = None
    tracker.remove_label.return_value = None
    tracker.ensure_label.return_value = None
    return tracker


def _make_telemetry() -> AsyncMock:
    tel = AsyncMock()
    tel.write.return_value = None
    return tel


def _make_ctx(
    tmp_path: Path,
    *,
    item_id: str = "42",
    work_branch: str = "cog/42-fix",
    telemetry: Any = None,
    event_sink: Any = None,
) -> ExecutionContext:
    sink = event_sink or RecordingEventSink()
    return ExecutionContext(
        project_dir=tmp_path,
        tmp_dir=tmp_path,
        state_cache=InMemoryStateCache(),
        headless=True,
        item=make_item(item_id=item_id, title="Fix the bug"),
        work_branch=work_branch,
        telemetry=telemetry,
        event_sink=sink,
    )


def _make_wf(
    tracker: AsyncMock | None = None,
    host: AsyncMock | None = None,
) -> RalphWorkflow:
    from tests.fakes import EchoRunner

    return RalphWorkflow(
        runner=EchoRunner(),
        tracker=tracker or _make_tracker(),
        host=host or _make_host(),
    )


@pytest.fixture(autouse=True)
def _writable_state_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))


# ---------------------------------------------------------------------------
# PrChecks dataclass predicates (unit tests)
# ---------------------------------------------------------------------------


def test_pr_checks_all_passed_empty() -> None:
    assert PrChecks(runs=()).all_passed is True


def test_pr_checks_all_passed_with_passed_and_skipped() -> None:
    runs = (
        CheckRun(name="ci", state="passed", link=""),
        CheckRun(name="docs", state="skipped", link=""),
    )
    checks = PrChecks(runs=runs)
    assert checks.all_passed is True
    assert checks.pending is False
    assert checks.failed == ()


def test_pr_checks_pending_when_any_pending() -> None:
    runs = (
        CheckRun(name="ci", state="passed", link=""),
        CheckRun(name="deploy", state="pending", link=""),
    )
    checks = PrChecks(runs=runs)
    assert checks.pending is True
    assert checks.all_passed is False


def test_pr_checks_failed_subset() -> None:
    runs = (
        CheckRun(name="ci", state="passed", link="https://pass"),
        CheckRun(name="lint", state="failed", link="https://fail"),
    )
    checks = PrChecks(runs=runs)
    assert len(checks.failed) == 1
    assert checks.failed[0].name == "lint"


# ---------------------------------------------------------------------------
# _wait_for_ci: basic behavior
# ---------------------------------------------------------------------------


async def test_wait_emits_start_event_immediately(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("COG_CI_POLL_INTERVAL_SECONDS", "0.001")
    monkeypatch.setenv("COG_CI_TIMEOUT_SECONDS", "10")
    sink = RecordingEventSink()
    host = _make_host(checks_sequence=[_passed_checks("ci")])
    wf = _make_wf(host=host)
    ctx = _make_ctx(tmp_path, event_sink=sink)

    await wf._wait_for_ci(ctx, _make_pr())

    status_messages = [e.message for e in sink.events if isinstance(e, StatusEvent)]
    assert any("Waiting for CI" in m for m in status_messages)
    assert status_messages[0].startswith("⏳ Waiting for CI")


async def test_wait_polls_until_all_checks_resolved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("COG_CI_POLL_INTERVAL_SECONDS", "0.001")
    monkeypatch.setenv("COG_CI_TIMEOUT_SECONDS", "10")
    # pending, pending, pending, then passed
    sequence = [
        _pending_checks("ci"),
        _pending_checks("ci"),
        _pending_checks("ci"),
        _passed_checks("ci"),
    ]
    host = _make_host(checks_sequence=sequence)
    wf = _make_wf(host=host)
    sink = RecordingEventSink()
    ctx = _make_ctx(tmp_path, event_sink=sink)

    result = await wf._wait_for_ci(ctx, _make_pr())

    assert result.all_passed is True
    assert host.get_pr_checks.call_count == 4


async def test_wait_emits_resolution_event_on_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("COG_CI_POLL_INTERVAL_SECONDS", "0.001")
    monkeypatch.setenv("COG_CI_TIMEOUT_SECONDS", "10")
    host = _make_host(checks_sequence=[_passed_checks("ci")])
    wf = _make_wf(host=host)
    sink = RecordingEventSink()
    ctx = _make_ctx(tmp_path, event_sink=sink)

    await wf._wait_for_ci(ctx, _make_pr())

    messages = [e.message for e in sink.events if isinstance(e, StatusEvent)]
    assert any("✓" in m and "passed" in m for m in messages)


async def test_wait_emits_resolution_event_on_fail_with_check_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("COG_CI_POLL_INTERVAL_SECONDS", "0.001")
    monkeypatch.setenv("COG_CI_TIMEOUT_SECONDS", "10")
    host = _make_host(checks_sequence=[_failed_checks("lint", "tests")])
    wf = _make_wf(host=host)
    sink = RecordingEventSink()
    ctx = _make_ctx(tmp_path, event_sink=sink)

    await wf._wait_for_ci(ctx, _make_pr())

    messages = [e.message for e in sink.events if isinstance(e, StatusEvent)]
    resolution = next(m for m in messages if "✗" in m)
    assert "lint" in resolution
    assert "tests" in resolution


async def test_wait_emits_heartbeat_every_60s(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Heartbeat emitted after 60s of waiting."""
    monkeypatch.setenv("COG_CI_POLL_INTERVAL_SECONDS", "0.001")
    monkeypatch.setenv("COG_CI_TIMEOUT_SECONDS", "10")

    async def fake_sleep(_duration: float) -> None:
        pass

    time_values = [0.0, 0.0, 61.0, 61.0, 61.0]  # advance past 60s on 3rd call
    time_idx = [0]

    def fake_monotonic() -> float:
        val = time_values[min(time_idx[0], len(time_values) - 1)]
        time_idx[0] += 1
        return val

    # Returns pending twice then passed
    sequence = [_pending_checks("ci"), _pending_checks("ci"), _passed_checks("ci")]
    host = _make_host(checks_sequence=sequence)
    wf = _make_wf(host=host)
    sink = RecordingEventSink()
    ctx = _make_ctx(tmp_path, event_sink=sink)

    with (
        patch("asyncio.sleep", side_effect=fake_sleep),
        patch("cog.workflows.ralph.time.monotonic", side_effect=fake_monotonic),
    ):
        await wf._wait_for_ci(ctx, _make_pr())

    messages = [e.message for e in sink.events if isinstance(e, StatusEvent)]
    heartbeats = [m for m in messages if "passed," in m and "pending" in m]
    assert len(heartbeats) >= 1


async def test_wait_does_not_emit_heartbeat_before_first_interval_elapses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("COG_CI_POLL_INTERVAL_SECONDS", "0.001")
    monkeypatch.setenv("COG_CI_TIMEOUT_SECONDS", "10")

    # Time never advances past 60s
    time_values = [0.0, 0.0, 1.0, 1.0, 2.0, 2.0]
    time_idx = [0]

    def fake_monotonic() -> float:
        val = time_values[min(time_idx[0], len(time_values) - 1)]
        time_idx[0] += 1
        return val

    async def fake_sleep(_: float) -> None:
        pass

    sequence = [_pending_checks("ci"), _passed_checks("ci")]
    host = _make_host(checks_sequence=sequence)
    wf = _make_wf(host=host)
    sink = RecordingEventSink()
    ctx = _make_ctx(tmp_path, event_sink=sink)

    with (
        patch("asyncio.sleep", side_effect=fake_sleep),
        patch("cog.workflows.ralph.time.monotonic", side_effect=fake_monotonic),
    ):
        await wf._wait_for_ci(ctx, _make_pr())

    messages = [e.message for e in sink.events if isinstance(e, StatusEvent)]
    heartbeats = [m for m in messages if "passed," in m and "pending" in m]
    assert len(heartbeats) == 0


async def test_wait_cancellation_propagates_cancelled_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("COG_CI_POLL_INTERVAL_SECONDS", "0.001")
    monkeypatch.setenv("COG_CI_TIMEOUT_SECONDS", "10")

    async def forever_pending(_: int) -> PrChecks:
        return _pending_checks("ci")

    host = _make_host()
    host.get_pr_checks.side_effect = forever_pending

    async def cancelling_sleep(_: float) -> None:
        raise asyncio.CancelledError

    wf = _make_wf(host=host)
    sink = RecordingEventSink()
    ctx = _make_ctx(tmp_path, event_sink=sink)

    with (
        patch("asyncio.sleep", side_effect=cancelling_sleep),
        pytest.raises(asyncio.CancelledError),
    ):
        await wf._wait_for_ci(ctx, _make_pr())


async def test_wait_honors_cog_ci_poll_interval_env_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("COG_CI_POLL_INTERVAL_SECONDS", "99.5")
    monkeypatch.setenv("COG_CI_TIMEOUT_SECONDS", "200")

    sleep_args: list[float] = []

    async def recording_sleep(duration: float) -> None:
        sleep_args.append(duration)

    host = _make_host(checks_sequence=[_pending_checks("ci"), _passed_checks("ci")])
    wf = _make_wf(host=host)
    sink = RecordingEventSink()
    ctx = _make_ctx(tmp_path, event_sink=sink)

    with patch("asyncio.sleep", side_effect=recording_sleep):
        await wf._wait_for_ci(ctx, _make_pr())

    assert any(abs(d - 99.5) < 0.01 for d in sleep_args)


async def test_wait_honors_cog_ci_timeout_env_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A very small timeout causes the wait to raise TimeoutError."""
    monkeypatch.setenv("COG_CI_POLL_INTERVAL_SECONDS", "0.001")
    monkeypatch.setenv("COG_CI_TIMEOUT_SECONDS", "0.01")

    async def always_pending(_: int) -> PrChecks:
        await asyncio.sleep(0.001)
        return _pending_checks("ci")

    host = _make_host()
    host.get_pr_checks.side_effect = always_pending
    wf = _make_wf(host=host)
    sink = RecordingEventSink()
    ctx = _make_ctx(tmp_path, event_sink=sink)

    with pytest.raises(TimeoutError):
        await wf._wait_for_ci(ctx, _make_pr())


# ---------------------------------------------------------------------------
# finalize_success + CI gate integration
# ---------------------------------------------------------------------------


async def test_finalize_success_calls_wait_for_ci_after_pr_creation(tmp_path: Path) -> None:
    host = _make_host(checks_sequence=[_passed_checks("ci")])
    wf = _make_wf(host=host)
    ctx = _make_ctx(tmp_path)

    with patch.object(wf, "_wait_for_ci", new_callable=AsyncMock) as mock_wait:
        mock_wait.return_value = _passed_checks("ci")
        await wf.finalize_success(ctx, [make_stage_result("build", commits=1)])

    mock_wait.assert_awaited_once()
    # Called with ctx and a PullRequest
    assert isinstance(mock_wait.call_args.args[1], PullRequest)


async def test_finalize_success_ci_pass_marks_processed_and_swaps_labels(tmp_path: Path) -> None:
    tracker = _make_tracker()
    host = _make_host(checks_sequence=[_passed_checks("ci")])
    wf = _make_wf(tracker=tracker, host=host)
    ctx = _make_ctx(tmp_path)

    with patch.object(wf, "_wait_for_ci", new_callable=AsyncMock) as mock_wait:
        mock_wait.return_value = _passed_checks("ci")
        await wf.finalize_success(ctx, [make_stage_result("build", commits=1)])

    assert ctx.state_cache.is_processed(ctx.item)
    removed = [c.args[1] for c in tracker.remove_label.call_args_list]
    assert "agent-ready" in removed


async def test_finalize_success_ci_pass_writes_telemetry_with_outcome_success(
    tmp_path: Path,
) -> None:
    tel = _make_telemetry()
    host = _make_host()
    wf = _make_wf(host=host)
    ctx = _make_ctx(tmp_path, telemetry=tel)

    with patch.object(wf, "_wait_for_ci", new_callable=AsyncMock) as mock_wait:
        mock_wait.return_value = _passed_checks("ci")
        await wf.finalize_success(ctx, [make_stage_result("build", commits=1)])

    tel.write.assert_awaited_once()
    assert tel.write.call_args.args[0].outcome == "success"


async def test_finalize_success_ci_fail_comments_on_pr_with_failing_checks(tmp_path: Path) -> None:
    host = _make_host()
    wf = _make_wf(host=host)
    ctx = _make_ctx(tmp_path)

    failing = PrChecks(runs=(CheckRun(name="lint", state="failed", link="https://ci/lint"),))
    with patch.object(wf, "_wait_for_ci", new_callable=AsyncMock) as mock_wait:
        mock_wait.return_value = failing
        await wf.finalize_success(ctx, [make_stage_result("build", commits=1)])

    host.comment_on_pr.assert_awaited_once()
    body = host.comment_on_pr.call_args.args[1]
    assert "lint" in body
    assert "https://ci/lint" in body


async def test_finalize_success_ci_fail_comments_on_tracker_item(tmp_path: Path) -> None:
    tracker = _make_tracker()
    host = _make_host()
    wf = _make_wf(tracker=tracker, host=host)
    ctx = _make_ctx(tmp_path)

    failing = _failed_checks("tests")
    with patch.object(wf, "_wait_for_ci", new_callable=AsyncMock) as mock_wait:
        mock_wait.return_value = failing
        await wf.finalize_success(ctx, [make_stage_result("build", commits=1)])

    # tracker.comment is called twice: once for PR URL, once for CI failure
    assert tracker.comment.call_count == 2
    ci_comment = tracker.comment.call_args_list[-1].args[1]
    assert "CI failed" in ci_comment


async def test_finalize_success_ci_fail_adds_agent_failed_label(tmp_path: Path) -> None:
    tracker = _make_tracker()
    host = _make_host()
    wf = _make_wf(tracker=tracker, host=host)
    ctx = _make_ctx(tmp_path)

    with patch.object(wf, "_wait_for_ci", new_callable=AsyncMock) as mock_wait:
        mock_wait.return_value = _failed_checks("ci")
        await wf.finalize_success(ctx, [make_stage_result("build", commits=1)])

    added = [c.args[1] for c in tracker.add_label.call_args_list]
    assert "agent-failed" in added


async def test_finalize_success_ci_fail_removes_agent_ready(tmp_path: Path) -> None:
    tracker = _make_tracker()
    host = _make_host()
    wf = _make_wf(tracker=tracker, host=host)
    ctx = _make_ctx(tmp_path)

    with patch.object(wf, "_wait_for_ci", new_callable=AsyncMock) as mock_wait:
        mock_wait.return_value = _failed_checks("ci")
        await wf.finalize_success(ctx, [make_stage_result("build", commits=1)])

    removed = [c.args[1] for c in tracker.remove_label.call_args_list]
    assert "agent-ready" in removed


async def test_finalize_success_ci_fail_marks_processed_with_ci_failed_outcome(
    tmp_path: Path,
) -> None:
    host = _make_host()
    wf = _make_wf(host=host)
    ctx = _make_ctx(tmp_path)

    with patch.object(wf, "_wait_for_ci", new_callable=AsyncMock) as mock_wait:
        mock_wait.return_value = _failed_checks("ci")
        await wf.finalize_success(ctx, [make_stage_result("build", commits=1)])

    assert ctx.state_cache.is_processed(ctx.item)
    assert ctx.state_cache._processed[ctx.state_cache._key(ctx.item)] == "ci-failed"


async def test_finalize_success_ci_fail_writes_telemetry_with_cause_class_ci_checks_failed_error(
    tmp_path: Path,
) -> None:
    tel = _make_telemetry()
    host = _make_host()
    wf = _make_wf(host=host)
    ctx = _make_ctx(tmp_path, telemetry=tel)

    with patch.object(wf, "_wait_for_ci", new_callable=AsyncMock) as mock_wait:
        mock_wait.return_value = _failed_checks("ci")
        await wf.finalize_success(ctx, [make_stage_result("build", commits=1)])

    tel.write.assert_awaited_once()
    record = tel.write.call_args.args[0]
    assert record.outcome == "ci-failed"
    assert record.cause_class == "CiChecksFailedError"


async def test_finalize_success_ci_timeout_writes_telemetry_with_cause_class_ci_timeout_error(
    tmp_path: Path,
) -> None:
    tel = _make_telemetry()
    host = _make_host()
    wf = _make_wf(host=host)
    ctx = _make_ctx(tmp_path, telemetry=tel)

    with patch.object(wf, "_wait_for_ci", new_callable=AsyncMock) as mock_wait:
        mock_wait.side_effect = TimeoutError()
        await wf.finalize_success(ctx, [make_stage_result("build", commits=1)])

    tel.write.assert_awaited_once()
    record = tel.write.call_args.args[0]
    assert record.outcome == "ci-failed"
    assert record.cause_class == "CiTimeoutError"
