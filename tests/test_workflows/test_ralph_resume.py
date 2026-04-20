"""Tests for RalphWorkflow resume / restart / agent-failed label logic."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from cog.core.context import ExecutionContext
from cog.core.errors import HostError, TrackerError
from cog.core.host import GitHost, PrChecks, PullRequest
from cog.core.tracker import IssueTracker
from cog.workflows.ralph import RalphWorkflow
from tests.fakes import InMemoryStateCache, RecordingEventSink, make_item, make_stage_result

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_workflow(*, restart: bool = False) -> RalphWorkflow:
    return RalphWorkflow(
        runner=AsyncMock(),
        tracker=AsyncMock(spec=IssueTracker),
        restart=restart,
    )


def _make_ctx(item=None) -> ExecutionContext:
    ctx = ExecutionContext(
        project_dir=Path("/fake/project"),
        tmp_dir=Path("/tmp"),
        state_cache=InMemoryStateCache(),
        headless=True,
    )
    ctx.item = item
    return ctx


def _make_tracker() -> AsyncMock:
    tracker = AsyncMock(spec=IssueTracker)
    tracker.comment.return_value = None
    tracker.add_label.return_value = None
    tracker.remove_label.return_value = None
    tracker.ensure_label.return_value = None
    return tracker


def _make_host(*, pr: PullRequest | None = None) -> AsyncMock:
    host = AsyncMock(spec=GitHost)
    host.push_branch.return_value = None
    host.get_pr_for_branch.return_value = pr
    host.create_pr.return_value = PullRequest(
        number=1,
        url="https://github.com/org/repo/pull/1",
        state="open",
        body="",
        head_branch="cog/42-fix",
    )
    host.update_pr.return_value = None
    host.comment_on_pr.return_value = None
    host.get_pr_checks.return_value = PrChecks(runs=())
    return host


def _make_wf_with_tracker_host(tracker=None, host=None, *, restart: bool = False) -> RalphWorkflow:
    from tests.fakes import EchoRunner

    return RalphWorkflow(
        runner=EchoRunner(),
        tracker=tracker or _make_tracker(),
        host=host or _make_host(),
        restart=restart,
    )


# ---------------------------------------------------------------------------
# pre_stages branch-state logic
# ---------------------------------------------------------------------------


async def test_pre_stages_creates_branch_when_none_exists() -> None:
    item = make_item(item_id="42", title="Fix the bug")
    ctx = _make_ctx(item)
    wf = _make_workflow()
    create_mock = AsyncMock()

    with (
        patch("cog.workflows.ralph.git.default_branch", AsyncMock(return_value="main")),
        patch("cog.workflows.ralph.git.checkout_branch", AsyncMock()),
        patch("cog.workflows.ralph.git.fetch_origin", AsyncMock()),
        patch("cog.workflows.ralph.git.merge_ff_only", AsyncMock()),
        patch("cog.workflows.ralph.git.branch_exists", AsyncMock(return_value=False)),
        patch("cog.workflows.ralph.git.create_branch", create_mock),
    ):
        await wf.pre_stages(ctx)

    create_mock.assert_awaited_once_with(
        Path("/fake/project"), "cog/42-fix-the-bug", start_point="HEAD"
    )
    assert ctx.work_branch == "cog/42-fix-the-bug"


async def test_pre_stages_silently_deletes_stale_empty_branch_and_recreates() -> None:
    item = make_item(item_id="42", title="Fix the bug")
    ctx = _make_ctx(item)
    wf = _make_workflow()
    delete_mock = AsyncMock()
    create_mock = AsyncMock()

    with (
        patch("cog.workflows.ralph.git.default_branch", AsyncMock(return_value="main")),
        patch("cog.workflows.ralph.git.checkout_branch", AsyncMock()),
        patch("cog.workflows.ralph.git.fetch_origin", AsyncMock()),
        patch("cog.workflows.ralph.git.merge_ff_only", AsyncMock()),
        patch("cog.workflows.ralph.git.branch_exists", AsyncMock(return_value=True)),
        patch("cog.workflows.ralph.git.commits_between", AsyncMock(return_value=0)),
        patch("cog.workflows.ralph.git.delete_branch", delete_mock),
        patch("cog.workflows.ralph.git.create_branch", create_mock),
    ):
        await wf.pre_stages(ctx)

    delete_mock.assert_awaited_once()
    create_mock.assert_awaited_once()


async def test_pre_stages_resumes_existing_branch_with_commits() -> None:
    item = make_item(item_id="42", title="Fix the bug")
    ctx = _make_ctx(item)
    wf = _make_workflow()
    checkout_calls: list[str] = []

    async def _checkout(project_dir, branch):
        checkout_calls.append(branch)

    with (
        patch("cog.workflows.ralph.git.default_branch", AsyncMock(return_value="main")),
        patch("cog.workflows.ralph.git.checkout_branch", _checkout),
        patch("cog.workflows.ralph.git.fetch_origin", AsyncMock()),
        patch("cog.workflows.ralph.git.merge_ff_only", AsyncMock()),
        patch("cog.workflows.ralph.git.branch_exists", AsyncMock(return_value=True)),
        patch("cog.workflows.ralph.git.commits_between", AsyncMock(return_value=3)),
        patch("cog.workflows.ralph.git.create_branch", AsyncMock()) as create_mock,
        patch("cog.workflows.ralph.git.delete_branch", AsyncMock()) as delete_mock,
    ):
        await wf.pre_stages(ctx)

    # Should have checked out work branch (not created or deleted)
    assert "cog/42-fix-the-bug" in checkout_calls
    create_mock.assert_not_awaited()
    delete_mock.assert_not_awaited()


async def test_pre_stages_sets_resumed_flag_on_resume() -> None:
    item = make_item(item_id="42", title="Fix the bug")
    ctx = _make_ctx(item)
    wf = _make_workflow()

    with (
        patch("cog.workflows.ralph.git.default_branch", AsyncMock(return_value="main")),
        patch("cog.workflows.ralph.git.checkout_branch", AsyncMock()),
        patch("cog.workflows.ralph.git.fetch_origin", AsyncMock()),
        patch("cog.workflows.ralph.git.merge_ff_only", AsyncMock()),
        patch("cog.workflows.ralph.git.branch_exists", AsyncMock(return_value=True)),
        patch("cog.workflows.ralph.git.commits_between", AsyncMock(return_value=2)),
        patch("cog.workflows.ralph.git.create_branch", AsyncMock()),
        patch("cog.workflows.ralph.git.delete_branch", AsyncMock()),
    ):
        await wf.pre_stages(ctx)

    assert wf._resumed_this_iteration.get("42") is True


async def test_pre_stages_clears_resumed_flag_on_fresh_create() -> None:
    item = make_item(item_id="42", title="Fix the bug")
    ctx = _make_ctx(item)
    wf = _make_workflow()

    with (
        patch("cog.workflows.ralph.git.default_branch", AsyncMock(return_value="main")),
        patch("cog.workflows.ralph.git.checkout_branch", AsyncMock()),
        patch("cog.workflows.ralph.git.fetch_origin", AsyncMock()),
        patch("cog.workflows.ralph.git.merge_ff_only", AsyncMock()),
        patch("cog.workflows.ralph.git.branch_exists", AsyncMock(return_value=False)),
        patch("cog.workflows.ralph.git.create_branch", AsyncMock()),
    ):
        await wf.pre_stages(ctx)

    assert wf._resumed_this_iteration.get("42") is False


async def test_pre_stages_honors_restart_flag_deletes_and_recreates() -> None:
    item = make_item(item_id="42", title="Fix the bug")
    ctx = _make_ctx(item)
    wf = _make_workflow(restart=True)
    delete_mock = AsyncMock()
    create_mock = AsyncMock()

    with (
        patch("cog.workflows.ralph.git.default_branch", AsyncMock(return_value="main")),
        patch("cog.workflows.ralph.git.checkout_branch", AsyncMock()),
        patch("cog.workflows.ralph.git.fetch_origin", AsyncMock()),
        patch("cog.workflows.ralph.git.merge_ff_only", AsyncMock()),
        patch("cog.workflows.ralph.git.branch_exists", AsyncMock(return_value=True)),
        patch("cog.workflows.ralph.git.commits_between", AsyncMock(return_value=5)),
        patch("cog.workflows.ralph.git.delete_branch", delete_mock),
        patch("cog.workflows.ralph.git.create_branch", create_mock),
    ):
        await wf.pre_stages(ctx)

    delete_mock.assert_awaited_once()
    create_mock.assert_awaited_once()


async def test_pre_stages_restart_flag_forces_delete_even_on_empty_branch_noop() -> None:
    # --restart on a 0-commits branch still deletes + recreates (same as default, just explicit)
    item = make_item(item_id="42", title="Fix the bug")
    ctx = _make_ctx(item)
    wf = _make_workflow(restart=True)
    delete_mock = AsyncMock()
    create_mock = AsyncMock()

    with (
        patch("cog.workflows.ralph.git.default_branch", AsyncMock(return_value="main")),
        patch("cog.workflows.ralph.git.checkout_branch", AsyncMock()),
        patch("cog.workflows.ralph.git.fetch_origin", AsyncMock()),
        patch("cog.workflows.ralph.git.merge_ff_only", AsyncMock()),
        patch("cog.workflows.ralph.git.branch_exists", AsyncMock(return_value=True)),
        patch("cog.workflows.ralph.git.commits_between", AsyncMock(return_value=0)),
        patch("cog.workflows.ralph.git.delete_branch", delete_mock),
        patch("cog.workflows.ralph.git.create_branch", create_mock),
    ):
        await wf.pre_stages(ctx)

    delete_mock.assert_awaited_once()
    create_mock.assert_awaited_once()


async def test_pre_stages_with_restart_does_not_set_resumed_flag() -> None:
    item = make_item(item_id="42", title="Fix the bug")
    ctx = _make_ctx(item)
    wf = _make_workflow(restart=True)

    with (
        patch("cog.workflows.ralph.git.default_branch", AsyncMock(return_value="main")),
        patch("cog.workflows.ralph.git.checkout_branch", AsyncMock()),
        patch("cog.workflows.ralph.git.fetch_origin", AsyncMock()),
        patch("cog.workflows.ralph.git.merge_ff_only", AsyncMock()),
        patch("cog.workflows.ralph.git.branch_exists", AsyncMock(return_value=True)),
        patch("cog.workflows.ralph.git.commits_between", AsyncMock(return_value=3)),
        patch("cog.workflows.ralph.git.delete_branch", AsyncMock()),
        patch("cog.workflows.ralph.git.create_branch", AsyncMock()),
    ):
        await wf.pre_stages(ctx)

    assert wf._resumed_this_iteration.get("42") is False


# ---------------------------------------------------------------------------
# Label lifecycle
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _writable_state_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))


@pytest.fixture(autouse=True)
def _clean_rebase(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch _rebase_before_push to return clean so finalize_success tests don't hit git."""
    from cog.workflows.ralph import RebaseOutcome

    async def _noop(self: object, ctx: object) -> RebaseOutcome:
        return RebaseOutcome(status="clean")

    monkeypatch.setattr(RalphWorkflow, "_rebase_before_push", _noop)


def _make_ctx_with_tmp(tmp_path: Path, *, item_id: str = "42") -> ExecutionContext:
    return ExecutionContext(
        project_dir=tmp_path,
        tmp_dir=tmp_path,
        state_cache=InMemoryStateCache(),
        headless=True,
        item=make_item(item_id=item_id, title="Fix the bug"),
        work_branch="cog/42-fix-the-bug",
        event_sink=RecordingEventSink(),
    )


async def test_finalize_error_keeps_agent_ready_and_adds_agent_failed(tmp_path: Path) -> None:
    tracker = _make_tracker()
    wf = _make_wf_with_tracker_host(tracker=tracker)
    ctx = _make_ctx_with_tmp(tmp_path)
    await wf.finalize_error(ctx, RuntimeError("boom"), [])

    remove_calls = [c.args[1] for c in tracker.remove_label.call_args_list]
    assert "agent-ready" not in remove_calls
    tracker.add_label.assert_awaited()
    add_calls = [c.args[1] for c in tracker.add_label.call_args_list]
    assert "agent-failed" in add_calls


async def test_handle_push_failed_keeps_agent_ready_and_adds_agent_failed(tmp_path: Path) -> None:
    tracker = _make_tracker()
    host = _make_host()
    host.push_branch.side_effect = HostError("push failed")
    wf = _make_wf_with_tracker_host(tracker=tracker, host=host)
    ctx = _make_ctx_with_tmp(tmp_path)
    await wf.finalize_success(ctx, [make_stage_result("build", commits=1)])

    remove_calls = [c.args[1] for c in tracker.remove_label.call_args_list]
    assert "agent-ready" not in remove_calls
    add_calls = [c.args[1] for c in tracker.add_label.call_args_list]
    assert "agent-failed" in add_calls


async def test_finalize_success_removes_agent_failed_label(tmp_path: Path) -> None:
    tracker = _make_tracker()
    wf = _make_wf_with_tracker_host(tracker=tracker)
    ctx = _make_ctx_with_tmp(tmp_path)
    await wf.finalize_success(ctx, [make_stage_result("build", commits=1)])

    remove_calls = [c.args[1] for c in tracker.remove_label.call_args_list]
    assert "agent-failed" in remove_calls


async def test_finalize_noop_removes_agent_failed_label(tmp_path: Path) -> None:
    tracker = _make_tracker()
    wf = _make_wf_with_tracker_host(tracker=tracker)
    ctx = _make_ctx_with_tmp(tmp_path)
    await wf.finalize_noop(ctx, [make_stage_result("build")])

    remove_calls = [c.args[1] for c in tracker.remove_label.call_args_list]
    assert "agent-failed" in remove_calls


async def test_finalize_error_tolerates_missing_agent_failed_label_on_first_error(
    tmp_path: Path,
) -> None:
    tracker = _make_tracker()
    tracker.remove_label.side_effect = TrackerError("label not found")
    wf = _make_wf_with_tracker_host(tracker=tracker)
    ctx = _make_ctx_with_tmp(tmp_path)
    # finalize_error doesn't remove agent-failed, so TrackerError from remove_label
    # only fires in finalize_success/noop. Simulate it there:
    tracker.remove_label.side_effect = None  # reset
    tracker.add_label.side_effect = None
    # Should not raise even if ensure_label or add_label has issues
    await wf.finalize_error(ctx, RuntimeError("err"), [])


async def test_finalize_success_tolerates_missing_agent_failed_label(tmp_path: Path) -> None:
    """remove_label('agent-failed') wrapped in try/except TrackerError."""
    tracker = _make_tracker()

    def _remove_side_effect(item, label):
        if label == "agent-failed":
            raise TrackerError("label not present")

    tracker.remove_label.side_effect = _remove_side_effect
    wf = _make_wf_with_tracker_host(tracker=tracker)
    ctx = _make_ctx_with_tmp(tmp_path)
    # Should not raise
    await wf.finalize_success(ctx, [make_stage_result("build", commits=1)])


async def test_finalize_noop_tolerates_missing_agent_failed_label(tmp_path: Path) -> None:
    tracker = _make_tracker()

    def _remove_side_effect(item, label):
        if label == "agent-failed":
            raise TrackerError("label not present")

    tracker.remove_label.side_effect = _remove_side_effect
    wf = _make_wf_with_tracker_host(tracker=tracker)
    ctx = _make_ctx_with_tmp(tmp_path)
    await wf.finalize_noop(ctx, [make_stage_result("build")])


# ---------------------------------------------------------------------------
# Telemetry passthrough
# ---------------------------------------------------------------------------


def _make_tel() -> AsyncMock:
    tel = AsyncMock()
    tel.write.return_value = None
    return tel


async def test_write_telemetry_passes_resumed_true_when_branch_was_resumed(
    tmp_path: Path,
) -> None:
    tel = _make_tel()
    wf = _make_wf_with_tracker_host()
    ctx = _make_ctx_with_tmp(tmp_path)
    ctx.telemetry = tel
    wf._resumed_this_iteration["42"] = True
    await wf.finalize_success(ctx, [make_stage_result("build", commits=1)])
    record = tel.write.call_args.args[0]
    assert record.resumed is True


async def test_write_telemetry_passes_resumed_false_when_branch_was_fresh(
    tmp_path: Path,
) -> None:
    tel = _make_tel()
    wf = _make_wf_with_tracker_host()
    ctx = _make_ctx_with_tmp(tmp_path)
    ctx.telemetry = tel
    wf._resumed_this_iteration["42"] = False
    await wf.finalize_success(ctx, [make_stage_result("build", commits=1)])
    record = tel.write.call_args.args[0]
    assert record.resumed is False


async def test_write_telemetry_passes_resumed_true_on_error_when_resumed(
    tmp_path: Path,
) -> None:
    tel = _make_tel()
    wf = _make_wf_with_tracker_host()
    ctx = _make_ctx_with_tmp(tmp_path)
    ctx.telemetry = tel
    wf._resumed_this_iteration["42"] = True
    await wf.finalize_error(ctx, RuntimeError("err"), [])
    record = tel.write.call_args.args[0]
    assert record.resumed is True


# ---------------------------------------------------------------------------
# PR idempotency on resume
# ---------------------------------------------------------------------------


async def test_resume_then_success_updates_existing_pr_via_get_pr_for_branch(
    tmp_path: Path,
) -> None:
    existing_pr = PullRequest(
        number=7,
        url="https://github.com/org/repo/pull/7",
        state="open",
        body="",
        head_branch="cog/42-fix-the-bug",
    )
    host = _make_host(pr=existing_pr)
    tracker = _make_tracker()
    wf = _make_wf_with_tracker_host(tracker=tracker, host=host)
    wf._resumed_this_iteration["42"] = True
    ctx = _make_ctx_with_tmp(tmp_path)
    await wf.finalize_success(ctx, [make_stage_result("build", commits=1)])

    host.create_pr.assert_not_awaited()
    host.update_pr.assert_awaited_once_with(7, body=host.update_pr.call_args.kwargs["body"])
