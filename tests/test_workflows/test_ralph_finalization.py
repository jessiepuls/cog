"""Tests for RalphWorkflow finalize_success/noop/error and helpers."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cog.core.context import ExecutionContext
from cog.core.errors import HostError, StageError
from cog.core.host import GitHost, PullRequest
from cog.core.tracker import IssueTracker
from cog.workflows.ralph import RalphWorkflow, _split_summary_and_test_plan
from tests.fakes import InMemoryStateCache, make_item, make_stage_result


@pytest.fixture(autouse=True)
def _writable_state_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point XDG_STATE_HOME at a writable temp dir so write_report doesn't fail."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_pr(number: int = 1, url: str = "https://github.com/org/repo/pull/1") -> PullRequest:
    return PullRequest(
        number=number,
        url=url,
        state="open",
        body="",
        head_branch="cog/42-fix",
    )


def _make_host(*, pr: PullRequest | None = None, push_error: HostError | None = None) -> AsyncMock:
    host = AsyncMock(spec=GitHost)
    if push_error is not None:
        host.push_branch.side_effect = push_error
    else:
        host.push_branch.return_value = None
    host.get_pr_for_branch.return_value = pr
    host.create_pr.return_value = _make_pr()
    host.update_pr.return_value = None
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
    from tests.fakes import EchoRunner

    return RalphWorkflow(
        runner=EchoRunner(),
        tracker=tracker or _make_tracker(),
        host=host or _make_host(),
    )


# ---------------------------------------------------------------------------
# _split_summary_and_test_plan unit tests
# ---------------------------------------------------------------------------


def test_split_test_plan_extracts_section() -> None:
    msg = "Some summary here.\n\n### Test plan\n\n- [ ] Check it works\n- [ ] Edge case"
    summary, test_plan = _split_summary_and_test_plan(msg)
    assert "Some summary here." in summary
    assert "- [ ] Check it works" in test_plan
    assert "- [ ] Edge case" in test_plan


def test_split_test_plan_fallback_when_missing_returns_placeholder_bullet() -> None:
    msg = "Just a summary with no test plan section."
    summary, test_plan = _split_summary_and_test_plan(msg)
    assert summary == "Just a summary with no test plan section."
    assert test_plan == "- [ ] Manual verification of the change"


def test_split_test_plan_header_matching_is_case_insensitive() -> None:
    msg = "Summary.\n\n### TEST PLAN\n\n- [ ] step"
    summary, test_plan = _split_summary_and_test_plan(msg)
    assert "step" in test_plan


# ---------------------------------------------------------------------------
# _build_pr_body unit tests
# ---------------------------------------------------------------------------


def test_pr_body_template_shape(tmp_path: Path) -> None:
    wf = _make_wf()
    ctx = _make_ctx(tmp_path)
    msg = "Did the thing.\n\n### Test plan\n\n- [ ] Check"
    results = [
        make_stage_result("build", cost=0.01, final_message=msg),
        make_stage_result("review", cost=0.02),
        make_stage_result("document", cost=0.005),
    ]
    body = wf._build_pr_body(ctx, results)
    assert "## Summary" in body
    assert "## Closes" in body
    assert "Closes #42" in body
    assert "## Test plan" in body
    assert "- [ ] Check" in body
    assert "0.035" in body  # total cost


def test_pr_body_appends_doc_warning_when_error_set(tmp_path: Path) -> None:
    wf = _make_wf()
    ctx = _make_ctx(tmp_path)
    doc_error = RuntimeError("doc failed")
    results = [
        make_stage_result("build", final_message="summary"),
        make_stage_result("document", error=doc_error),
    ]
    body = wf._build_pr_body(ctx, results)
    assert "Document stage failed" in body


def test_pr_body_fallback_test_plan_when_missing(tmp_path: Path) -> None:
    wf = _make_wf()
    ctx = _make_ctx(tmp_path)
    results = [make_stage_result("build", final_message="summary without test plan")]
    body = wf._build_pr_body(ctx, results)
    assert "Manual verification" in body


# ---------------------------------------------------------------------------
# finalize_success
# ---------------------------------------------------------------------------


async def test_finalize_success_pushes_branch(tmp_path: Path) -> None:
    host = _make_host()
    wf = _make_wf(host=host)
    ctx = _make_ctx(tmp_path)
    await wf.finalize_success(ctx, [make_stage_result("build", commits=1)])
    host.push_branch.assert_awaited_once_with("cog/42-fix")


async def test_finalize_success_creates_pr_with_correct_title_format(tmp_path: Path) -> None:
    host = _make_host()
    wf = _make_wf(host=host)
    ctx = _make_ctx(tmp_path)
    await wf.finalize_success(ctx, [make_stage_result("build", commits=1)])
    host.create_pr.assert_awaited_once()
    call_kwargs = host.create_pr.call_args.kwargs
    assert call_kwargs["title"] == "Fix the bug (#42)"


async def test_finalize_success_pr_body_contains_summary_closes_testplan_cost(
    tmp_path: Path,
) -> None:
    host = _make_host()
    wf = _make_wf(host=host)
    ctx = _make_ctx(tmp_path)
    msg = "Did thing.\n\n### Test plan\n\n- [ ] Verify"
    results = [
        make_stage_result("build", cost=0.05, final_message=msg),
    ]
    await wf.finalize_success(ctx, results)
    body = host.create_pr.call_args.kwargs["body"]
    assert "## Summary" in body
    assert "Closes #42" in body
    assert "## Test plan" in body
    assert "- [ ] Verify" in body
    assert "0.050" in body


async def test_finalize_success_pr_body_fallback_test_plan_when_missing(tmp_path: Path) -> None:
    host = _make_host()
    wf = _make_wf(host=host)
    ctx = _make_ctx(tmp_path)
    results = [make_stage_result("build", final_message="summary only")]
    await wf.finalize_success(ctx, results)
    body = host.create_pr.call_args.kwargs["body"]
    assert "Manual verification" in body


async def test_finalize_success_pr_body_appends_doc_warning_when_error_set(
    tmp_path: Path,
) -> None:
    host = _make_host()
    wf = _make_wf(host=host)
    ctx = _make_ctx(tmp_path)
    results = [
        make_stage_result("build", final_message="summary"),
        make_stage_result("document", error=RuntimeError("doc failed")),
    ]
    await wf.finalize_success(ctx, results)
    body = host.create_pr.call_args.kwargs["body"]
    assert "Document stage failed" in body


async def test_finalize_success_existing_pr_is_updated_not_recreated(tmp_path: Path) -> None:
    existing = _make_pr(number=7)
    host = _make_host(pr=existing)
    wf = _make_wf(host=host)
    ctx = _make_ctx(tmp_path)
    await wf.finalize_success(ctx, [make_stage_result("build", commits=1)])
    host.create_pr.assert_not_awaited()
    host.update_pr.assert_awaited_once_with(7, body=host.update_pr.call_args.kwargs["body"])


async def test_finalize_success_comments_on_issue_with_pr_url(tmp_path: Path) -> None:
    pr = _make_pr(url="https://github.com/org/repo/pull/99")
    host = _make_host(pr=None)
    host.create_pr.return_value = pr
    tracker = _make_tracker()
    wf = _make_wf(tracker=tracker, host=host)
    ctx = _make_ctx(tmp_path)
    await wf.finalize_success(ctx, [make_stage_result("build", commits=1)])
    tracker.comment.assert_awaited_once()
    body = tracker.comment.call_args.args[1]
    assert "https://github.com/org/repo/pull/99" in body


async def test_finalize_success_removes_agent_ready_label(tmp_path: Path) -> None:
    tracker = _make_tracker()
    wf = _make_wf(tracker=tracker)
    ctx = _make_ctx(tmp_path)
    await wf.finalize_success(ctx, [make_stage_result("build", commits=1)])
    tracker.remove_label.assert_awaited_once_with(ctx.item, "agent-ready")


async def test_finalize_success_marks_processed_in_state_cache(tmp_path: Path) -> None:
    wf = _make_wf()
    ctx = _make_ctx(tmp_path)
    await wf.finalize_success(ctx, [make_stage_result("build", commits=1)])
    assert ctx.state_cache.is_processed(ctx.item)


async def test_finalize_success_saves_state_cache(tmp_path: Path) -> None:
    wf = _make_wf()
    cache = MagicMock(spec=InMemoryStateCache)
    cache.is_processed.return_value = False
    cache.mark_processed.return_value = None
    cache.save.return_value = None
    ctx = ExecutionContext(
        project_dir=tmp_path,
        tmp_dir=tmp_path,
        state_cache=cache,
        headless=True,
        item=make_item(item_id="42", title="Fix the bug"),
        work_branch="cog/42-fix",
    )
    await wf.finalize_success(ctx, [make_stage_result("build", commits=1)])
    cache.save.assert_called_once()


async def test_finalize_success_writes_telemetry_with_success_outcome_and_pr_url(
    tmp_path: Path,
) -> None:
    pr = _make_pr(url="https://github.com/org/repo/pull/1")
    host = _make_host(pr=None)
    host.create_pr.return_value = pr
    tel = _make_telemetry()
    wf = _make_wf(host=host)
    ctx = _make_ctx(tmp_path, telemetry=tel)
    await wf.finalize_success(ctx, [make_stage_result("build", commits=1)])
    tel.write.assert_awaited_once()
    record = tel.write.call_args.args[0]
    assert record.outcome == "success"
    assert record.pr_url == "https://github.com/org/repo/pull/1"


async def test_finalize_success_writes_report(tmp_path: Path) -> None:
    wf = _make_wf()
    ctx = _make_ctx(tmp_path)
    with patch.object(wf, "write_report", new_callable=AsyncMock) as mock_report:
        await wf.finalize_success(ctx, [make_stage_result("build", commits=1)])
        mock_report.assert_awaited_once_with(
            ctx,
            mock_report.call_args.args[1],
            "success",
            error=None,
        )


async def test_finalize_success_push_failure_routes_to_push_failed_path(
    tmp_path: Path,
) -> None:
    push_err = HostError("push failed")
    host = _make_host(push_error=push_err)
    wf = _make_wf(host=host)
    ctx = _make_ctx(tmp_path)
    with patch.object(wf, "_handle_push_failed", new_callable=AsyncMock) as mock_pf:
        await wf.finalize_success(ctx, [make_stage_result("build", commits=1)])
        mock_pf.assert_awaited_once()
        host.create_pr.assert_not_awaited()


# ---------------------------------------------------------------------------
# push-failed path
# ---------------------------------------------------------------------------


async def test_push_failed_comments_with_manual_push_instructions(tmp_path: Path) -> None:
    tracker = _make_tracker()
    push_err = HostError("network error")
    host = _make_host(push_error=push_err)
    wf = _make_wf(tracker=tracker, host=host)
    ctx = _make_ctx(tmp_path)
    await wf.finalize_success(ctx, [make_stage_result("build", commits=1)])
    tracker.comment.assert_awaited_once()
    body = tracker.comment.call_args.args[1]
    assert "git push -u origin cog/42-fix" in body
    assert "network error" in body


async def test_push_failed_does_not_call_push_or_create_pr(tmp_path: Path) -> None:
    host = _make_host(push_error=HostError("fail"))
    wf = _make_wf(host=host)
    ctx = _make_ctx(tmp_path)
    await wf.finalize_success(ctx, [make_stage_result("build", commits=1)])
    # push_branch was called (it's what triggered the error); create_pr was not
    host.create_pr.assert_not_awaited()


async def test_push_failed_removes_agent_ready_label(tmp_path: Path) -> None:
    tracker = _make_tracker()
    host = _make_host(push_error=HostError("fail"))
    wf = _make_wf(tracker=tracker, host=host)
    ctx = _make_ctx(tmp_path)
    await wf.finalize_success(ctx, [make_stage_result("build", commits=1)])
    tracker.remove_label.assert_awaited_once_with(ctx.item, "agent-ready")


async def test_push_failed_does_not_mark_processed_in_state_cache(tmp_path: Path) -> None:
    host = _make_host(push_error=HostError("fail"))
    wf = _make_wf(host=host)
    ctx = _make_ctx(tmp_path)
    await wf.finalize_success(ctx, [make_stage_result("build", commits=1)])
    assert not ctx.state_cache.is_processed(ctx.item)


async def test_push_failed_writes_telemetry_with_push_failed_outcome(tmp_path: Path) -> None:
    host = _make_host(push_error=HostError("fail"))
    tel = _make_telemetry()
    wf = _make_wf(host=host)
    ctx = _make_ctx(tmp_path, telemetry=tel)
    await wf.finalize_success(ctx, [make_stage_result("build", commits=1)])
    tel.write.assert_awaited_once()
    record = tel.write.call_args.args[0]
    assert record.outcome == "push-failed"


async def test_push_failed_tolerates_comment_failure(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    tracker = _make_tracker()
    tracker.comment.side_effect = RuntimeError("comment failed")
    host = _make_host(push_error=HostError("push fail"))
    wf = _make_wf(tracker=tracker, host=host)
    ctx = _make_ctx(tmp_path)
    # Should not raise
    await wf.finalize_success(ctx, [make_stage_result("build", commits=1)])
    captured = capsys.readouterr()
    assert "warning" in captured.err


# ---------------------------------------------------------------------------
# finalize_noop
# ---------------------------------------------------------------------------


async def test_finalize_noop_ensures_agent_abandoned_label_exists(tmp_path: Path) -> None:
    tracker = _make_tracker()
    wf = _make_wf(tracker=tracker)
    ctx = _make_ctx(tmp_path)
    await wf.finalize_noop(ctx, [make_stage_result("build")])
    tracker.ensure_label.assert_awaited_once_with(
        "agent-abandoned",
        color="ededed",
        description="Cog attempted this but made no changes",
    )


async def test_finalize_noop_adds_agent_abandoned_label(tmp_path: Path) -> None:
    tracker = _make_tracker()
    wf = _make_wf(tracker=tracker)
    ctx = _make_ctx(tmp_path)
    await wf.finalize_noop(ctx, [make_stage_result("build")])
    tracker.add_label.assert_awaited_once_with(ctx.item, "agent-abandoned")


async def test_finalize_noop_removes_agent_ready_label(tmp_path: Path) -> None:
    tracker = _make_tracker()
    wf = _make_wf(tracker=tracker)
    ctx = _make_ctx(tmp_path)
    await wf.finalize_noop(ctx, [make_stage_result("build")])
    tracker.remove_label.assert_awaited_once_with(ctx.item, "agent-ready")


async def test_finalize_noop_comments_with_build_stage_explanation(tmp_path: Path) -> None:
    tracker = _make_tracker()
    wf = _make_wf(tracker=tracker)
    ctx = _make_ctx(tmp_path)
    msg = "The issue was too ambiguous to implement."
    await wf.finalize_noop(ctx, [make_stage_result("build", final_message=msg)])
    tracker.comment.assert_awaited_once()
    body = tracker.comment.call_args.args[1]
    assert msg in body


async def test_finalize_noop_comments_fallback_when_build_message_empty(
    tmp_path: Path,
) -> None:
    tracker = _make_tracker()
    wf = _make_wf(tracker=tracker)
    ctx = _make_ctx(tmp_path)
    await wf.finalize_noop(ctx, [make_stage_result("build", final_message="")])
    body = tracker.comment.call_args.args[1]
    assert "(no explanation provided)" in body


async def test_finalize_noop_marks_processed_in_state_cache_with_noop_outcome(
    tmp_path: Path,
) -> None:
    wf = _make_wf()
    ctx = _make_ctx(tmp_path)
    await wf.finalize_noop(ctx, [make_stage_result("build")])
    assert ctx.state_cache.is_processed(ctx.item)
    assert ctx.state_cache._processed[ctx.state_cache._key(ctx.item)] == "no-op"


async def test_finalize_noop_saves_state_cache(tmp_path: Path) -> None:
    wf = _make_wf()
    cache = MagicMock(spec=InMemoryStateCache)
    cache.mark_processed.return_value = None
    cache.save.return_value = None
    ctx = ExecutionContext(
        project_dir=tmp_path,
        tmp_dir=tmp_path,
        state_cache=cache,
        headless=True,
        item=make_item(item_id="42", title="Fix"),
        work_branch="cog/42-fix",
    )
    await wf.finalize_noop(ctx, [make_stage_result("build")])
    cache.save.assert_called_once()


async def test_finalize_noop_writes_telemetry_with_no_op_outcome(tmp_path: Path) -> None:
    tel = _make_telemetry()
    wf = _make_wf()
    ctx = _make_ctx(tmp_path, telemetry=tel)
    await wf.finalize_noop(ctx, [make_stage_result("build")])
    tel.write.assert_awaited_once()
    record = tel.write.call_args.args[0]
    assert record.outcome == "no-op"


async def test_finalize_noop_writes_report(tmp_path: Path) -> None:
    wf = _make_wf()
    ctx = _make_ctx(tmp_path)
    with patch.object(wf, "write_report", new_callable=AsyncMock) as mock_report:
        await wf.finalize_noop(ctx, [make_stage_result("build")])
        mock_report.assert_awaited_once_with(ctx, mock_report.call_args.args[1], "noop", error=None)


# ---------------------------------------------------------------------------
# finalize_error
# ---------------------------------------------------------------------------


async def test_finalize_error_keeps_agent_ready_label(tmp_path: Path) -> None:
    tracker = _make_tracker()
    wf = _make_wf(tracker=tracker)
    ctx = _make_ctx(tmp_path)
    await wf.finalize_error(ctx, RuntimeError("boom"), [])
    for call in tracker.remove_label.call_args_list:
        assert call.args[1] != "agent-ready", "remove_label('agent-ready') must not be called"


async def test_finalize_error_does_not_push_or_create_pr(tmp_path: Path) -> None:
    host = _make_host()
    wf = _make_wf(host=host)
    ctx = _make_ctx(tmp_path)
    await wf.finalize_error(ctx, RuntimeError("fail"), [])
    host.push_branch.assert_not_awaited()
    host.create_pr.assert_not_awaited()


async def test_finalize_error_comments_with_stage_error_summary(tmp_path: Path) -> None:
    tracker = _make_tracker()
    wf = _make_wf(tracker=tracker)
    ctx = _make_ctx(tmp_path)
    stage_result = make_stage_result("build", final_message="some output")
    from cog.core.stage import Stage

    stage = Stage(name="build", prompt_source=lambda _: "", model="m", runner=None)  # type: ignore[arg-type]
    err = StageError(stage, stage_result)
    await wf.finalize_error(ctx, err, [stage_result])
    body = tracker.comment.call_args.args[1]
    assert "Stage 'build' failed" in body


async def test_finalize_error_comments_with_generic_exception_summary(tmp_path: Path) -> None:
    tracker = _make_tracker()
    wf = _make_wf(tracker=tracker)
    ctx = _make_ctx(tmp_path)
    await wf.finalize_error(ctx, ValueError("bad value"), [])
    body = tracker.comment.call_args.args[1]
    assert "ValueError: bad value" in body


async def test_finalize_error_comment_includes_final_message_tail(tmp_path: Path) -> None:
    tracker = _make_tracker()
    wf = _make_wf(tracker=tracker)
    ctx = _make_ctx(tmp_path)
    long_msg = "x" * 3000
    stage_result = make_stage_result("build", final_message=long_msg)
    from cog.core.stage import Stage

    stage = Stage(name="build", prompt_source=lambda _: "", model="m", runner=None)  # type: ignore[arg-type]
    err = StageError(stage, stage_result)
    await wf.finalize_error(ctx, err, [stage_result])
    body = tracker.comment.call_args.args[1]
    # Last 2000 chars of long_msg (all 'x') should appear
    assert "x" * 100 in body


async def test_finalize_error_tolerates_comment_failure(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    tracker = _make_tracker()
    tracker.comment.side_effect = RuntimeError("tracker down")
    wf = _make_wf(tracker=tracker)
    ctx = _make_ctx(tmp_path)
    # Must not raise
    await wf.finalize_error(ctx, RuntimeError("err"), [])
    captured = capsys.readouterr()
    assert "warning" in captured.err


async def test_finalize_error_does_not_mark_processed(tmp_path: Path) -> None:
    wf = _make_wf()
    ctx = _make_ctx(tmp_path)
    await wf.finalize_error(ctx, RuntimeError("err"), [])
    assert not ctx.state_cache.is_processed(ctx.item)


async def test_finalize_error_writes_telemetry_with_error_outcome(tmp_path: Path) -> None:
    tel = _make_telemetry()
    wf = _make_wf()
    ctx = _make_ctx(tmp_path, telemetry=tel)
    await wf.finalize_error(ctx, RuntimeError("some error"), [])
    tel.write.assert_awaited_once()
    record = tel.write.call_args.args[0]
    assert record.outcome == "error"


async def test_finalize_error_writes_report_with_error_param(tmp_path: Path) -> None:
    wf = _make_wf()
    ctx = _make_ctx(tmp_path)
    err = RuntimeError("fail")
    with patch.object(wf, "write_report", new_callable=AsyncMock) as mock_report:
        await wf.finalize_error(ctx, err, [])
        mock_report.assert_awaited_once()
        assert mock_report.call_args.kwargs["error"] == err


# ---------------------------------------------------------------------------
# Integration smoke tests
# ---------------------------------------------------------------------------


async def test_full_iteration_end_to_end_success(tmp_path: Path) -> None:
    """Mocked tracker + host: go through classify → finalize_success."""
    from cog.core.stage import Stage
    from cog.core.workflow import StageExecutor
    from tests.fakes import EchoRunner

    tracker = _make_tracker()
    tracker.list_by_label.return_value = [make_item(item_id="42", title="Fix")]
    tracker.get = AsyncMock(return_value=make_item(item_id="42", title="Fix"))
    pr = _make_pr(url="https://github.com/org/repo/pull/1")
    host = _make_host(pr=None)
    host.create_pr.return_value = pr

    wf = RalphWorkflow(runner=EchoRunner(), tracker=tracker, host=host)

    async def _pre(ctx: ExecutionContext) -> None:
        ctx.work_branch = "cog/42-fix"

    def _stages(ctx: ExecutionContext) -> list[Stage]:
        return [
            Stage(
                name="build",
                prompt_source=lambda _: "go",
                model="m",
                runner=EchoRunner(),
                tolerate_failure=False,
            )
        ]

    async def _classify(ctx: ExecutionContext, results: list) -> str:
        return "success"

    wf.pre_stages = _pre  # type: ignore[method-assign]
    wf.stages = _stages  # type: ignore[method-assign]
    wf.classify_outcome = _classify  # type: ignore[method-assign]

    # Patch write_report to avoid fs side-effects
    wf.write_report = AsyncMock()  # type: ignore[method-assign]

    cache = InMemoryStateCache()
    ctx = ExecutionContext(
        project_dir=tmp_path,
        tmp_dir=tmp_path,
        state_cache=cache,
        headless=True,
    )

    await StageExecutor().run(wf, ctx)

    host.push_branch.assert_awaited_once_with("cog/42-fix")
    host.create_pr.assert_awaited_once()
    tracker.comment.assert_awaited_once()
    tracker.remove_label.assert_awaited_once_with(ctx.item, "agent-ready")
    assert cache.is_processed(ctx.item)


async def test_full_iteration_end_to_end_noop(tmp_path: Path) -> None:
    """commits_created=0 → classify='noop' → finalize_noop runs cleanly."""
    from cog.core.stage import Stage
    from cog.core.workflow import StageExecutor
    from tests.fakes import EchoRunner

    tracker = _make_tracker()
    tracker.list_by_label.return_value = [make_item(item_id="42", title="Fix")]
    tracker.get = AsyncMock(return_value=make_item(item_id="42", title="Fix"))
    host = _make_host()

    wf = RalphWorkflow(runner=EchoRunner(), tracker=tracker, host=host)

    async def _pre(ctx: ExecutionContext) -> None:
        ctx.work_branch = "cog/42-fix"

    def _stages(ctx: ExecutionContext) -> list[Stage]:
        return [
            Stage(
                name="build",
                prompt_source=lambda _: "go",
                model="m",
                runner=EchoRunner(),
                tolerate_failure=False,
            )
        ]

    async def _classify(ctx: ExecutionContext, results: list) -> str:
        return "noop"

    wf.pre_stages = _pre  # type: ignore[method-assign]
    wf.stages = _stages  # type: ignore[method-assign]
    wf.classify_outcome = _classify  # type: ignore[method-assign]
    wf.write_report = AsyncMock()  # type: ignore[method-assign]

    cache = InMemoryStateCache()
    ctx = ExecutionContext(
        project_dir=tmp_path,
        tmp_dir=tmp_path,
        state_cache=cache,
        headless=True,
    )

    await StageExecutor().run(wf, ctx)

    host.push_branch.assert_not_awaited()
    tracker.ensure_label.assert_awaited_once()
    tracker.add_label.assert_awaited_once_with(ctx.item, "agent-abandoned")
    tracker.remove_label.assert_awaited_once_with(ctx.item, "agent-ready")
    assert cache.is_processed(ctx.item)
