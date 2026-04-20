"""Tests for RalphWorkflow rebase-before-push logic."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from cog.core.context import ExecutionContext
from cog.core.errors import GitError
from cog.core.host import GitHost
from cog.core.outcomes import StageResult
from cog.core.tracker import IssueTracker
from cog.core.workflow import StageExecutor
from cog.workflows.ralph import RalphWorkflow, RebaseOutcome
from tests.fakes import (
    EchoRunner,
    InMemoryStateCache,
    make_item,
    make_stage_result,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _writable_state_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))


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
    telemetry: object = None,
) -> ExecutionContext:
    return ExecutionContext(
        project_dir=tmp_path,
        tmp_dir=tmp_path,
        state_cache=InMemoryStateCache(),
        headless=True,
        item=make_item(item_id=item_id, title="Fix the bug"),
        work_branch=work_branch,
        telemetry=telemetry,
    )


def _make_wf(
    tracker: AsyncMock | None = None,
    host: AsyncMock | None = None,
) -> RalphWorkflow:
    return RalphWorkflow(
        runner=EchoRunner(),
        tracker=tracker or _make_tracker(),
        host=host or AsyncMock(spec=GitHost),
    )


def _patch_git(**kwargs: object):
    """Return a context manager that patches multiple git module functions."""
    return patch.multiple("cog.workflows.ralph.git", **kwargs)


def _patch_run_stage(result: StageResult):
    """Patch StageExecutor._run_stage to return a fixed result."""
    return patch.object(StageExecutor, "_run_stage", new_callable=AsyncMock, return_value=result)


def _make_clean_stage_result(final_message: str = "") -> StageResult:
    return make_stage_result("rebase", final_message=final_message)


# ---------------------------------------------------------------------------
# _rebase_before_push — pre-check
# ---------------------------------------------------------------------------


async def test_rebase_skipped_when_work_branch_not_behind_default(tmp_path: Path) -> None:
    wf = _make_wf()
    ctx = _make_ctx(tmp_path)
    with _patch_git(
        default_branch=AsyncMock(return_value="main"),
        fetch_origin=AsyncMock(return_value=None),
        commits_between=AsyncMock(return_value=0),
    ):
        with patch.object(StageExecutor, "_run_stage", new_callable=AsyncMock) as mock_run:
            outcome = await wf._rebase_before_push(ctx)

    assert outcome.status == "clean"
    mock_run.assert_not_awaited()


async def test_rebase_invoked_when_work_branch_behind_default(tmp_path: Path) -> None:
    wf = _make_wf()
    ctx = _make_ctx(tmp_path)
    stage_result = _make_clean_stage_result()
    with _patch_git(
        default_branch=AsyncMock(return_value="main"),
        fetch_origin=AsyncMock(return_value=None),
        commits_between=AsyncMock(return_value=2),
        rebase_in_progress=AsyncMock(return_value=False),
    ):
        with _patch_run_stage(stage_result):
            outcome = await wf._rebase_before_push(ctx)

    assert outcome.status == "clean"


async def test_rebase_pre_check_fetches_origin_before_comparing(tmp_path: Path) -> None:
    wf = _make_wf()
    ctx = _make_ctx(tmp_path)
    fetch_calls: list[object] = []

    async def _fetch(project_dir: Path) -> None:
        fetch_calls.append(project_dir)

    with _patch_git(
        default_branch=AsyncMock(return_value="main"),
        fetch_origin=_fetch,
        commits_between=AsyncMock(return_value=0),
    ):
        await wf._rebase_before_push(ctx)

    assert len(fetch_calls) == 1


async def test_rebase_pre_check_git_error_falls_through_to_stage_invocation(
    tmp_path: Path,
) -> None:
    wf = _make_wf()
    ctx = _make_ctx(tmp_path)
    stage_result = _make_clean_stage_result()
    with _patch_git(
        default_branch=AsyncMock(return_value="main"),
        fetch_origin=AsyncMock(return_value=None),
        commits_between=AsyncMock(side_effect=GitError("rev-list failed")),
        rebase_in_progress=AsyncMock(return_value=False),
    ):
        with patch.object(
            StageExecutor, "_run_stage", new_callable=AsyncMock, return_value=stage_result
        ) as mock_run:
            outcome = await wf._rebase_before_push(ctx)

    # Stage must have been invoked (behind=1 assumed on git error)
    mock_run.assert_awaited_once()
    assert outcome.status == "clean"


# ---------------------------------------------------------------------------
# _rebase_before_push — stage properties
# ---------------------------------------------------------------------------


async def test_rebase_stage_uses_rebase_prompt_not_build_prompt(tmp_path: Path) -> None:
    wf = _make_wf()
    ctx = _make_ctx(tmp_path)
    captured_stages: list[object] = []

    async def _capture(self: object, stage: object, ctx: object) -> StageResult:
        captured_stages.append(stage)
        return _make_clean_stage_result()

    with _patch_git(
        default_branch=AsyncMock(return_value="main"),
        fetch_origin=AsyncMock(return_value=None),
        commits_between=AsyncMock(return_value=1),
        rebase_in_progress=AsyncMock(return_value=False),
    ):
        with patch.object(StageExecutor, "_run_stage", _capture):
            await wf._rebase_before_push(ctx)

    assert len(captured_stages) == 1
    stage = captured_stages[0]
    # Prompt source should produce the rebase prompt content
    from cog.core.stage import Stage

    assert isinstance(stage, Stage)
    prompt_text = stage.prompt_source(ctx)
    assert "git rebase" in prompt_text
    assert "Ralph: build stage" not in prompt_text


async def test_rebase_stage_inherits_cog_ralph_build_model_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("COG_RALPH_BUILD_MODEL", "claude-haiku-4-5-20251001")
    wf = _make_wf()
    ctx = _make_ctx(tmp_path)
    captured_stages: list[object] = []

    async def _capture(self: object, stage: object, ctx: object) -> StageResult:
        captured_stages.append(stage)
        return _make_clean_stage_result()

    with _patch_git(
        default_branch=AsyncMock(return_value="main"),
        fetch_origin=AsyncMock(return_value=None),
        commits_between=AsyncMock(return_value=1),
        rebase_in_progress=AsyncMock(return_value=False),
    ):
        with patch.object(StageExecutor, "_run_stage", _capture):
            await wf._rebase_before_push(ctx)

    from cog.core.stage import Stage

    stage = captured_stages[0]
    assert isinstance(stage, Stage)
    assert stage.model == "claude-haiku-4-5-20251001"


async def test_rebase_prompt_does_not_inject_git_state_content(tmp_path: Path) -> None:
    """Regression guard: rebase stage must use on-demand pattern (no pre-injected state)."""
    wf = _make_wf()
    ctx = _make_ctx(tmp_path)
    captured_stages: list[object] = []

    async def _capture(self: object, stage: object, ctx: object) -> StageResult:
        captured_stages.append(stage)
        return _make_clean_stage_result()

    with _patch_git(
        default_branch=AsyncMock(return_value="main"),
        fetch_origin=AsyncMock(return_value=None),
        commits_between=AsyncMock(return_value=1),
        rebase_in_progress=AsyncMock(return_value=False),
    ):
        with patch.object(StageExecutor, "_run_stage", _capture):
            await wf._rebase_before_push(ctx)

    from cog.core.stage import Stage

    stage = captured_stages[0]
    assert isinstance(stage, Stage)
    prompt_text = stage.prompt_source(ctx)
    # Must not contain injected git status or branch state
    assert "git status" not in prompt_text
    assert "HEAD is" not in prompt_text


# ---------------------------------------------------------------------------
# _rebase_before_push — clean path
# ---------------------------------------------------------------------------


async def test_clean_rebase_after_claude_proceeds_returns_clean(tmp_path: Path) -> None:
    wf = _make_wf()
    ctx = _make_ctx(tmp_path)
    stage_result = _make_clean_stage_result("Rebase complete.")
    with _patch_git(
        default_branch=AsyncMock(return_value="main"),
        fetch_origin=AsyncMock(return_value=None),
        commits_between=AsyncMock(return_value=3),
        rebase_in_progress=AsyncMock(return_value=False),
    ):
        with _patch_run_stage(stage_result):
            outcome = await wf._rebase_before_push(ctx)

    assert outcome.status == "clean"
    assert outcome.final_message == ""


# ---------------------------------------------------------------------------
# _rebase_before_push — conflict / mid-rebase path
# ---------------------------------------------------------------------------


async def test_claude_left_mid_rebase_triggers_abort(tmp_path: Path) -> None:
    wf = _make_wf()
    ctx = _make_ctx(tmp_path)
    stage_result = _make_clean_stage_result("Both sides changed foo().")
    abort_calls: list[object] = []

    async def _fake_abort(project_dir: Path) -> None:
        abort_calls.append(project_dir)

    with _patch_git(
        default_branch=AsyncMock(return_value="main"),
        fetch_origin=AsyncMock(return_value=None),
        commits_between=AsyncMock(return_value=1),
        rebase_in_progress=AsyncMock(return_value=True),
        rebase_abort=_fake_abort,
    ):
        with _patch_run_stage(stage_result):
            outcome = await wf._rebase_before_push(ctx)

    assert outcome.status == "conflict"
    assert len(abort_calls) == 1


async def test_abort_invokes_git_rebase_abort(tmp_path: Path) -> None:
    wf = _make_wf()
    ctx = _make_ctx(tmp_path)
    stage_result = _make_clean_stage_result()
    mock_abort = AsyncMock(return_value=None)
    with _patch_git(
        default_branch=AsyncMock(return_value="main"),
        fetch_origin=AsyncMock(return_value=None),
        commits_between=AsyncMock(return_value=1),
        rebase_in_progress=AsyncMock(return_value=True),
        rebase_abort=mock_abort,
    ):
        with _patch_run_stage(stage_result):
            await wf._rebase_before_push(ctx)

    mock_abort.assert_awaited_once_with(tmp_path)


async def test_unresolved_conflict_returns_conflict_with_final_message(tmp_path: Path) -> None:
    wf = _make_wf()
    ctx = _make_ctx(tmp_path)
    stage_result = _make_clean_stage_result("foo() defined differently on both sides.")
    with _patch_git(
        default_branch=AsyncMock(return_value="main"),
        fetch_origin=AsyncMock(return_value=None),
        commits_between=AsyncMock(return_value=1),
        rebase_in_progress=AsyncMock(return_value=True),
        rebase_abort=AsyncMock(return_value=None),
    ):
        with _patch_run_stage(stage_result):
            outcome = await wf._rebase_before_push(ctx)

    assert outcome.status == "conflict"
    assert "foo() defined differently" in outcome.final_message


# ---------------------------------------------------------------------------
# _rebase_before_push — helper outcome contracts
# ---------------------------------------------------------------------------


async def test_rebase_before_push_helper_returns_clean_outcome_when_precheck_skips(
    tmp_path: Path,
) -> None:
    wf = _make_wf()
    ctx = _make_ctx(tmp_path)
    with _patch_git(
        default_branch=AsyncMock(return_value="main"),
        fetch_origin=AsyncMock(return_value=None),
        commits_between=AsyncMock(return_value=0),
    ):
        outcome = await wf._rebase_before_push(ctx)

    assert isinstance(outcome, RebaseOutcome)
    assert outcome.status == "clean"


async def test_rebase_before_push_helper_returns_clean_outcome_when_stage_completes(
    tmp_path: Path,
) -> None:
    wf = _make_wf()
    ctx = _make_ctx(tmp_path)
    stage_result = _make_clean_stage_result()
    with _patch_git(
        default_branch=AsyncMock(return_value="main"),
        fetch_origin=AsyncMock(return_value=None),
        commits_between=AsyncMock(return_value=1),
        rebase_in_progress=AsyncMock(return_value=False),
    ):
        with _patch_run_stage(stage_result):
            outcome = await wf._rebase_before_push(ctx)

    assert isinstance(outcome, RebaseOutcome)
    assert outcome.status == "clean"


async def test_rebase_before_push_helper_returns_conflict_outcome_when_mid_rebase_after_claude(
    tmp_path: Path,
) -> None:
    wf = _make_wf()
    ctx = _make_ctx(tmp_path)
    stage_result = _make_clean_stage_result("analysis")
    with _patch_git(
        default_branch=AsyncMock(return_value="main"),
        fetch_origin=AsyncMock(return_value=None),
        commits_between=AsyncMock(return_value=1),
        rebase_in_progress=AsyncMock(return_value=True),
        rebase_abort=AsyncMock(return_value=None),
    ):
        with _patch_run_stage(stage_result):
            outcome = await wf._rebase_before_push(ctx)

    assert isinstance(outcome, RebaseOutcome)
    assert outcome.status == "conflict"


async def test_rebase_before_push_helper_aborts_mid_rebase_state_before_returning(
    tmp_path: Path,
) -> None:
    wf = _make_wf()
    ctx = _make_ctx(tmp_path)
    stage_result = _make_clean_stage_result()
    abort_called = []
    with _patch_git(
        default_branch=AsyncMock(return_value="main"),
        fetch_origin=AsyncMock(return_value=None),
        commits_between=AsyncMock(return_value=1),
        rebase_in_progress=AsyncMock(return_value=True),
        rebase_abort=AsyncMock(side_effect=lambda d: abort_called.append(d)),
    ):
        with _patch_run_stage(stage_result):
            outcome = await wf._rebase_before_push(ctx)

    assert len(abort_called) == 1
    assert outcome.status == "conflict"


# ---------------------------------------------------------------------------
# _handle_rebase_conflict — label / state behavior
# ---------------------------------------------------------------------------


async def test_handle_rebase_conflict_keeps_agent_ready(tmp_path: Path) -> None:
    tracker = _make_tracker()
    wf = _make_wf(tracker=tracker)
    ctx = _make_ctx(tmp_path)
    with _patch_git(default_branch=AsyncMock(return_value="main")):
        with patch.object(wf, "write_report", new_callable=AsyncMock):
            await wf._handle_rebase_conflict(ctx, [], "analysis")

    removed = [c.args[1] for c in tracker.remove_label.call_args_list]
    assert "agent-ready" not in removed


async def test_handle_rebase_conflict_adds_agent_failed(tmp_path: Path) -> None:
    tracker = _make_tracker()
    wf = _make_wf(tracker=tracker)
    ctx = _make_ctx(tmp_path)
    with _patch_git(default_branch=AsyncMock(return_value="main")):
        with patch.object(wf, "write_report", new_callable=AsyncMock):
            await wf._handle_rebase_conflict(ctx, [], "analysis")

    added = [c.args[1] for c in tracker.add_label.call_args_list]
    assert "agent-failed" in added


async def test_handle_rebase_conflict_does_not_mark_processed(tmp_path: Path) -> None:
    wf = _make_wf()
    ctx = _make_ctx(tmp_path)
    with _patch_git(default_branch=AsyncMock(return_value="main")):
        with patch.object(wf, "write_report", new_callable=AsyncMock):
            await wf._handle_rebase_conflict(ctx, [], "analysis")

    assert not ctx.state_cache.is_processed(ctx.item)


async def test_handle_rebase_conflict_adds_to_processed_this_loop_set(tmp_path: Path) -> None:
    wf = _make_wf()
    ctx = _make_ctx(tmp_path)
    with _patch_git(default_branch=AsyncMock(return_value="main")):
        with patch.object(wf, "write_report", new_callable=AsyncMock):
            await wf._handle_rebase_conflict(ctx, [], "analysis")

    assert (ctx.item.tracker_id, ctx.item.item_id) in wf._processed_this_loop


async def test_handle_rebase_conflict_comments_on_item_with_claude_analysis(
    tmp_path: Path,
) -> None:
    tracker = _make_tracker()
    wf = _make_wf(tracker=tracker)
    ctx = _make_ctx(tmp_path)
    with _patch_git(default_branch=AsyncMock(return_value="main")):
        with patch.object(wf, "write_report", new_callable=AsyncMock):
            await wf._handle_rebase_conflict(ctx, [], "Both sides changed bar()")

    tracker.comment.assert_awaited_once()
    body = tracker.comment.call_args.args[1]
    assert "Both sides changed bar()" in body
    assert "origin/main" in body


# ---------------------------------------------------------------------------
# _handle_rebase_conflict — telemetry
# ---------------------------------------------------------------------------


async def test_rebase_conflict_writes_telemetry_with_outcome_rebase_conflict(
    tmp_path: Path,
) -> None:
    tel = _make_telemetry()
    wf = _make_wf()
    ctx = _make_ctx(tmp_path, telemetry=tel)
    with _patch_git(default_branch=AsyncMock(return_value="main")):
        with patch.object(wf, "write_report", new_callable=AsyncMock):
            await wf._handle_rebase_conflict(ctx, [], "analysis")

    tel.write.assert_awaited_once()
    record = tel.write.call_args.args[0]
    assert record.outcome == "rebase-conflict"


async def test_rebase_conflict_writes_telemetry_with_cause_class_rebase_unresolved_error(
    tmp_path: Path,
) -> None:
    tel = _make_telemetry()
    wf = _make_wf()
    ctx = _make_ctx(tmp_path, telemetry=tel)
    with _patch_git(default_branch=AsyncMock(return_value="main")):
        with patch.object(wf, "write_report", new_callable=AsyncMock):
            await wf._handle_rebase_conflict(ctx, [], "analysis")

    record = tel.write.call_args.args[0]
    assert record.cause_class == "RebaseUnresolvedError"


async def test_clean_rebase_does_not_add_rebase_stage_to_telemetry_when_precheck_skipped(
    tmp_path: Path,
) -> None:
    """When pre-check skips the stage, no rebase stage row appears in telemetry."""
    tel = _make_telemetry()
    wf = _make_wf()
    ctx = _make_ctx(tmp_path, telemetry=tel)
    with _patch_git(
        default_branch=AsyncMock(return_value="main"),
        fetch_origin=AsyncMock(return_value=None),
        commits_between=AsyncMock(return_value=0),
    ):
        outcome = await wf._rebase_before_push(ctx)

    assert outcome.status == "clean"
    # No telemetry written by the helper itself on clean pre-check skip
    tel.write.assert_not_awaited()


# ---------------------------------------------------------------------------
# finalize_success integration with _rebase_before_push
# ---------------------------------------------------------------------------


async def test_finalize_success_calls_rebase_before_push(tmp_path: Path) -> None:
    from cog.core.host import PrChecks, PullRequest

    host = AsyncMock(spec=GitHost)
    host.push_branch.return_value = None
    host.get_pr_for_branch.return_value = None
    pr = PullRequest(
        number=1,
        url="https://example.com/pr/1",
        state="open",
        body="",
        head_branch="cog/42-fix",
    )
    host.create_pr.return_value = pr

    wf = _make_wf(host=host)
    ctx = _make_ctx(tmp_path)

    rebase_called = []

    async def _fake_rebase(self_: object, ctx_: object) -> RebaseOutcome:
        rebase_called.append(True)
        return RebaseOutcome(status="clean")

    async def _fake_wait_ci(self_: object, ctx_: object, pr_: object) -> PrChecks:
        return PrChecks(runs=())  # all_passed=True (no failing checks)

    with patch.object(RalphWorkflow, "_rebase_before_push", _fake_rebase):
        with patch.object(RalphWorkflow, "_wait_for_ci", _fake_wait_ci):
            with patch.object(wf, "write_report", new_callable=AsyncMock):
                await wf.finalize_success(ctx, [make_stage_result("build", commits=1)])

    assert rebase_called


async def test_finalize_success_skips_push_on_conflict(tmp_path: Path) -> None:
    host = AsyncMock(spec=GitHost)
    wf = _make_wf(host=host)
    ctx = _make_ctx(tmp_path)

    async def _fake_rebase(self_: object, ctx_: object) -> RebaseOutcome:
        return RebaseOutcome(status="conflict", final_message="incompatible")

    with patch.object(RalphWorkflow, "_rebase_before_push", _fake_rebase):
        with patch.object(wf, "_handle_rebase_conflict", new_callable=AsyncMock) as mock_conflict:
            await wf.finalize_success(ctx, [make_stage_result("build", commits=1)])

    host.push_branch.assert_not_awaited()
    mock_conflict.assert_awaited_once()


async def test_finalize_success_routes_to_handle_rebase_conflict_with_final_message(
    tmp_path: Path,
) -> None:
    host = AsyncMock(spec=GitHost)
    wf = _make_wf(host=host)
    ctx = _make_ctx(tmp_path)

    async def _fake_rebase(self_: object, ctx_: object) -> RebaseOutcome:
        return RebaseOutcome(status="conflict", final_message="the message")

    with patch.object(RalphWorkflow, "_rebase_before_push", _fake_rebase):
        with patch.object(wf, "_handle_rebase_conflict", new_callable=AsyncMock) as mock_conflict:
            await wf.finalize_success(ctx, [make_stage_result("build", commits=1)])

    _call_args = mock_conflict.call_args
    assert _call_args.args[2] == "the message"


# ---------------------------------------------------------------------------
# #101 integration skeleton — rebase helper shared with fix-push path
# ---------------------------------------------------------------------------


async def test_rebase_before_push_is_accessible_as_shared_helper(tmp_path: Path) -> None:
    """_rebase_before_push is a method on RalphWorkflow available to call sites."""
    wf = _make_wf()
    assert hasattr(wf, "_rebase_before_push")
    assert callable(wf._rebase_before_push)
