"""Tests for RalphWorkflow CI fix retry loop."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from cog.core.context import ExecutionContext
from cog.core.host import CheckRun, GitHost, PrChecks, PullRequest
from cog.core.runner import AgentRunner, ResultEvent, RunResult
from cog.core.tracker import IssueTracker
from cog.workflows.ralph import (
    RalphWorkflow,
    _dedupe_attempt_checks,
    _format_cap_comment,
)
from tests.fakes import (
    InMemoryStateCache,
    RecordingEventSink,
    ScriptedFinalMessageRunner,
    make_item,
    make_stage_result,
)

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _make_pr(number: int = 42, url: str = "https://github.com/org/repo/pull/42") -> PullRequest:
    return PullRequest(number=number, url=url, state="open", body="", head_branch="cog/42-fix")


def _passed_checks(*names: str) -> PrChecks:
    runs = tuple(CheckRun(name=n, state="passed", link=f"https://ci/{n}") for n in names)
    return PrChecks(runs=runs)


def _failed_checks(*names: str) -> PrChecks:
    runs = tuple(CheckRun(name=n, state="failed", link=f"https://ci/{n}") for n in names)
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
    return ExecutionContext(
        project_dir=tmp_path,
        tmp_dir=tmp_path,
        state_cache=InMemoryStateCache(),
        headless=True,
        item=make_item(item_id=item_id, title="Fix the bug"),
        work_branch=work_branch,
        telemetry=telemetry,
        event_sink=event_sink or RecordingEventSink(),
    )


@pytest.fixture(autouse=True)
def _writable_state_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))


class CommitOnceRunner(AgentRunner):
    """Runner that creates one commit the first time, zero on subsequent calls."""

    def __init__(self, project_dir: Path, message: str = "fix: CI failure") -> None:
        self._project_dir = project_dir
        self._message = message
        self._call_count = 0

    async def stream(self, prompt: str, *, model: str):
        import subprocess

        self._call_count += 1
        if self._call_count == 1:
            subprocess.run(
                ["git", "commit", "--allow-empty", "-m", self._message],
                cwd=self._project_dir,
                capture_output=True,
                check=True,
            )
        yield ResultEvent(
            result=RunResult(
                final_message="Fixed the failure.",
                total_cost_usd=0.1,
                exit_status=0,
                stream_json_path=Path("/dev/null"),
                duration_seconds=1.0,
            )
        )


# ---------------------------------------------------------------------------
# _dedupe_attempt_checks unit tests
# ---------------------------------------------------------------------------


def test_dedupe_attempt_checks_deduplicates_across_attempts() -> None:
    history = [(0, ("test_x", "lint")), (1, ("test_x", "test_y"))]
    result = _dedupe_attempt_checks(history)
    assert result == ("test_x", "lint", "test_y")


def test_dedupe_attempt_checks_empty_history() -> None:
    assert _dedupe_attempt_checks([]) == ()


def test_dedupe_attempt_checks_single_attempt() -> None:
    history = [(0, ("test_a", "test_b"))]
    assert _dedupe_attempt_checks(history) == ("test_a", "test_b")


def test_dedupe_attempt_checks_preserves_order() -> None:
    history = [(0, ("z", "a")), (1, ("b",))]
    assert _dedupe_attempt_checks(history) == ("z", "a", "b")


# ---------------------------------------------------------------------------
# _format_cap_comment unit tests
# ---------------------------------------------------------------------------


def test_cap_comment_flags_same_check_repeated() -> None:
    history = [(0, ("test_x", "lint")), (1, ("test_x",)), (2, ("test_x",))]
    comment = _format_cap_comment(history, retries_done=2)
    assert "flaky or environment-specific" in comment
    assert "test_x" in comment


def test_cap_comment_flags_different_checks_per_attempt() -> None:
    history = [(0, ("test_x",)), (1, ("test_y",))]
    comment = _format_cap_comment(history, retries_done=1)
    assert "regressions" in comment


def test_cap_comment_lists_all_attempt_outcomes() -> None:
    history = [(0, ("test_x",)), (1, ("test_y",))]
    comment = _format_cap_comment(history, retries_done=1)
    assert "Attempt 0" in comment
    assert "Attempt 1" in comment
    assert "test_x" in comment
    assert "test_y" in comment


def test_cap_comment_shows_attempt_count() -> None:
    history = [(0, ("test_x",)), (1, ("test_x",)), (2, ("test_x",))]
    comment = _format_cap_comment(history, retries_done=2)
    assert "3" in comment  # total attempts shown


# ---------------------------------------------------------------------------
# Retry loop behavior
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ci_fail_triggers_fix_stage_not_full_pipeline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CI failure triggers ci-fix stage, NOT build/review/document."""
    monkeypatch.setenv("COG_CI_POLL_INTERVAL_SECONDS", "0.01")
    monkeypatch.setenv("COG_CI_MAX_RETRIES", "1")

    host = _make_host(checks_sequence=[_failed_checks("test_x"), _passed_checks("test_x")])
    tracker = _make_tracker()
    ctx = _make_ctx(tmp_path)

    stage_names: list[str] = []

    async def fake_run_stage(self: object, stage: object, ctx: object) -> object:
        stage_names.append(getattr(stage, "name", "?"))
        return make_stage_result(
            getattr(stage, "name", "build"),
            commits=1,
            final_message="Fixed it.",
        )

    with patch("cog.core.workflow.StageExecutor._run_stage", fake_run_stage):
        wf = RalphWorkflow(runner=ScriptedFinalMessageRunner(""), tracker=tracker, host=host)
        pr = _make_pr()
        results = [make_stage_result("build", commits=1)]
        await wf._handle_ci_failure(ctx, results, pr, _failed_checks("test_x"))

    assert any(n.startswith("ci-fix-") for n in stage_names)
    assert "build" not in stage_names
    assert "review" not in stage_names
    assert "document" not in stage_names


@pytest.mark.asyncio
async def test_retry_uses_ci_fix_not_build_prompt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ci-fix stage loads ci_fix.md, not build.md."""
    monkeypatch.setenv("COG_CI_MAX_RETRIES", "1")

    captured_prompts: list[str] = []

    class CapturingRunner(AgentRunner):
        async def stream(self, prompt: str, *, model: str):
            captured_prompts.append(prompt)
            yield ResultEvent(
                result=RunResult(
                    final_message="fixed",
                    total_cost_usd=0.0,
                    exit_status=0,
                    stream_json_path=Path("/dev/null"),
                    duration_seconds=0.0,
                )
            )

    host = _make_host(checks_sequence=[_passed_checks("ci")])
    ctx = _make_ctx(tmp_path)

    async def fake_run_stage(self: object, stage: object, ctx_arg: object) -> object:
        ps = getattr(stage, "prompt_source", None)
        if ps:
            captured_prompts.append(ps(ctx_arg))
        return make_stage_result(getattr(stage, "name", "ci-fix"), commits=1)

    with patch("cog.core.workflow.StageExecutor._run_stage", fake_run_stage):
        wf = RalphWorkflow(runner=CapturingRunner(), tracker=_make_tracker(), host=host)
        pr = _make_pr()
        await wf._handle_ci_failure(ctx, [], pr, _failed_checks("test_x"))

    assert captured_prompts, "No prompts were captured"
    prompt = captured_prompts[0]
    assert "Ralph: CI fix stage" in prompt
    assert "Reproduce the failure" in prompt


@pytest.mark.asyncio
async def test_retry_inherits_build_model_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COG_CI_MAX_RETRIES", "1")
    monkeypatch.setenv("COG_RALPH_BUILD_MODEL", "claude-opus-4-6")

    stage_models: list[str] = []

    async def fake_run_stage(self: object, stage: object, ctx_arg: object) -> object:
        stage_models.append(getattr(stage, "model", "?"))
        return make_stage_result(getattr(stage, "name", "ci-fix"), commits=1)

    host = _make_host(checks_sequence=[_passed_checks("ci")])
    ctx = _make_ctx(tmp_path)

    with patch("cog.core.workflow.StageExecutor._run_stage", fake_run_stage):
        wf = RalphWorkflow(
            runner=ScriptedFinalMessageRunner(""), tracker=_make_tracker(), host=host
        )
        await wf._handle_ci_failure(ctx, [], _make_pr(), _failed_checks("test_x"))

    assert stage_models[0] == "claude-opus-4-6"


@pytest.mark.asyncio
async def test_retry_prompt_includes_failing_check_names_and_links(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COG_CI_MAX_RETRIES", "1")

    captured: list[str] = []

    async def fake_run_stage(self: object, stage: object, ctx_arg: object) -> object:
        ps = getattr(stage, "prompt_source", None)
        if ps:
            captured.append(ps(ctx_arg))
        return make_stage_result(getattr(stage, "name", "ci-fix"), commits=0)

    ctx = _make_ctx(tmp_path)
    checks = _failed_checks("my_test", "lint")

    with patch("cog.core.workflow.StageExecutor._run_stage", fake_run_stage):
        wf = RalphWorkflow(
            runner=ScriptedFinalMessageRunner(""), tracker=_make_tracker(), host=_make_host()
        )
        await wf._handle_ci_failure(ctx, [], _make_pr(), checks)

    assert captured
    assert "my_test" in captured[0]
    assert "lint" in captured[0]
    assert "https://ci/my_test" in captured[0]


@pytest.mark.asyncio
async def test_retry_prompt_does_not_inject_ci_logs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression guard: CI logs must NOT be pre-injected into the prompt."""
    monkeypatch.setenv("COG_CI_MAX_RETRIES", "1")

    captured: list[str] = []

    async def fake_run_stage(self: object, stage: object, ctx_arg: object) -> object:
        ps = getattr(stage, "prompt_source", None)
        if ps:
            captured.append(ps(ctx_arg))
        return make_stage_result(getattr(stage, "name", "ci-fix"), commits=0)

    ctx = _make_ctx(tmp_path)

    with patch("cog.core.workflow.StageExecutor._run_stage", fake_run_stage):
        wf = RalphWorkflow(
            runner=ScriptedFinalMessageRunner(""), tracker=_make_tracker(), host=_make_host()
        )
        await wf._handle_ci_failure(ctx, [], _make_pr(), _failed_checks("test_x"))

    assert captured
    # Prompt must not contain pre-injected log content (only instructions to fetch)
    assert "::error::" not in captured[0]
    assert "Run failed" not in captured[0]
    assert "##[error]" not in captured[0]


@pytest.mark.asyncio
async def test_retry_with_commit_pushes_and_re_waits_for_ci(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COG_CI_MAX_RETRIES", "1")
    monkeypatch.setenv("COG_CI_POLL_INTERVAL_SECONDS", "0.01")

    host = _make_host(checks_sequence=[_passed_checks("ci")])
    tracker = _make_tracker()
    ctx = _make_ctx(tmp_path)

    async def fake_run_stage(self: object, stage: object, ctx_arg: object) -> object:
        return make_stage_result(getattr(stage, "name", "ci-fix"), commits=1)

    with patch("cog.core.workflow.StageExecutor._run_stage", fake_run_stage):
        wf = RalphWorkflow(runner=ScriptedFinalMessageRunner(""), tracker=tracker, host=host)
        await wf._handle_ci_failure(ctx, [], _make_pr(), _failed_checks("test_x"))

    host.push_branch.assert_called_once()
    host.get_pr_checks.assert_called()


@pytest.mark.asyncio
async def test_retry_increments_per_item_retry_counter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COG_CI_MAX_RETRIES", "2")
    monkeypatch.setenv("COG_CI_POLL_INTERVAL_SECONDS", "0.01")

    # First CI wait fails, second passes
    host = _make_host(checks_sequence=[_failed_checks("test_x"), _passed_checks("ci")])
    ctx = _make_ctx(tmp_path)

    call_count = 0

    async def fake_run_stage(self: object, stage: object, ctx_arg: object) -> object:
        nonlocal call_count
        call_count += 1
        return make_stage_result(getattr(stage, "name", "ci-fix"), commits=1)

    with patch("cog.core.workflow.StageExecutor._run_stage", fake_run_stage):
        wf = RalphWorkflow(
            runner=ScriptedFinalMessageRunner(""), tracker=_make_tracker(), host=host
        )
        await wf._handle_ci_failure(ctx, [], _make_pr(), _failed_checks("test_x"))

    assert wf._ci_retries.get("42", 0) == 2


@pytest.mark.asyncio
async def test_retry_counter_is_per_item_not_global(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COG_CI_MAX_RETRIES", "1")
    monkeypatch.setenv("COG_CI_POLL_INTERVAL_SECONDS", "0.01")

    host = _make_host(checks_sequence=[_passed_checks("ci"), _passed_checks("ci")])
    tracker = _make_tracker()

    async def fake_run_stage(self: object, stage: object, ctx_arg: object) -> object:
        return make_stage_result(getattr(stage, "name", "ci-fix"), commits=1)

    ctx_1 = _make_ctx(tmp_path, item_id="1")
    ctx_2 = _make_ctx(tmp_path, item_id="2")

    with patch("cog.core.workflow.StageExecutor._run_stage", fake_run_stage):
        wf = RalphWorkflow(runner=ScriptedFinalMessageRunner(""), tracker=tracker, host=host)
        await wf._handle_ci_failure(ctx_1, [], _make_pr(), _failed_checks("test_x"))
        await wf._handle_ci_failure(ctx_2, [], _make_pr(), _failed_checks("test_y"))

    assert wf._ci_retries.get("1", 0) == 1
    assert wf._ci_retries.get("2", 0) == 1


# ---------------------------------------------------------------------------
# Cap enforcement
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retry_cap_default_is_2(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default cap is 2; after 2 fix attempts the item is abandoned."""
    monkeypatch.setenv("COG_CI_POLL_INTERVAL_SECONDS", "0.01")
    # Remove override so we use the default
    monkeypatch.delenv("COG_CI_MAX_RETRIES", raising=False)

    fix_call_count = 0

    async def fake_run_stage(self: object, stage: object, ctx_arg: object) -> object:
        nonlocal fix_call_count
        if getattr(stage, "name", "").startswith("ci-fix"):
            fix_call_count += 1
        return make_stage_result(getattr(stage, "name", "ci-fix"), commits=1)

    # Always fail CI to exhaust cap
    host = _make_host(
        checks_sequence=[
            _failed_checks("test_x"),
            _failed_checks("test_x"),
        ]
    )
    ctx = _make_ctx(tmp_path)

    with patch("cog.core.workflow.StageExecutor._run_stage", fake_run_stage):
        wf = RalphWorkflow(
            runner=ScriptedFinalMessageRunner(""), tracker=_make_tracker(), host=host
        )
        await wf._handle_ci_failure(ctx, [], _make_pr(), _failed_checks("test_x"))

    assert fix_call_count == 2


@pytest.mark.asyncio
async def test_retry_cap_honors_env_override(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COG_CI_MAX_RETRIES", "1")
    monkeypatch.setenv("COG_CI_POLL_INTERVAL_SECONDS", "0.01")

    fix_call_count = 0

    async def fake_run_stage(self: object, stage: object, ctx_arg: object) -> object:
        nonlocal fix_call_count
        if getattr(stage, "name", "").startswith("ci-fix"):
            fix_call_count += 1
        return make_stage_result(getattr(stage, "name", "ci-fix"), commits=1)

    host = _make_host(checks_sequence=[_failed_checks("test_x")])
    ctx = _make_ctx(tmp_path)

    with patch("cog.core.workflow.StageExecutor._run_stage", fake_run_stage):
        wf = RalphWorkflow(
            runner=ScriptedFinalMessageRunner(""), tracker=_make_tracker(), host=host
        )
        await wf._handle_ci_failure(ctx, [], _make_pr(), _failed_checks("test_x"))

    assert fix_call_count == 1


@pytest.mark.asyncio
async def test_retry_cap_zero_means_first_failure_abandons_immediately(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COG_CI_MAX_RETRIES", "0")

    fix_call_count = 0

    async def fake_run_stage(self: object, stage: object, ctx_arg: object) -> object:
        nonlocal fix_call_count
        if getattr(stage, "name", "").startswith("ci-fix"):
            fix_call_count += 1
        return make_stage_result(getattr(stage, "name", "ci-fix"), commits=0)

    ctx = _make_ctx(tmp_path)
    tracker = _make_tracker()

    with patch("cog.core.workflow.StageExecutor._run_stage", fake_run_stage):
        wf = RalphWorkflow(
            runner=ScriptedFinalMessageRunner(""), tracker=tracker, host=_make_host()
        )
        await wf._handle_ci_failure(ctx, [], _make_pr(), _failed_checks("test_x"))

    assert fix_call_count == 0
    # Should have been abandoned
    tracker.add_label.assert_called()


@pytest.mark.asyncio
async def test_retry_cap_exhaustion_calls_abandon(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COG_CI_MAX_RETRIES", "1")
    monkeypatch.setenv("COG_CI_POLL_INTERVAL_SECONDS", "0.01")

    async def fake_run_stage(self: object, stage: object, ctx_arg: object) -> object:
        return make_stage_result(getattr(stage, "name", "ci-fix"), commits=1)

    host = _make_host(checks_sequence=[_failed_checks("test_x")])
    ctx = _make_ctx(tmp_path)
    tracker = _make_tracker()

    with patch("cog.core.workflow.StageExecutor._run_stage", fake_run_stage):
        wf = RalphWorkflow(runner=ScriptedFinalMessageRunner(""), tracker=tracker, host=host)
        await wf._handle_ci_failure(ctx, [], _make_pr(), _failed_checks("test_x"))

    # Verify that add_label was called with agent-failed (second arg check)
    add_label_calls = [call[0][1] for call in tracker.add_label.call_args_list]
    assert "agent-failed" in add_label_calls


# ---------------------------------------------------------------------------
# No-commit / unreproducible path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_commit_on_fix_stage_treats_as_unreproducible(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COG_CI_MAX_RETRIES", "1")

    async def fake_run_stage(self: object, stage: object, ctx_arg: object) -> object:
        return make_stage_result(getattr(stage, "name", "ci-fix"), commits=0)

    ctx = _make_ctx(tmp_path)
    tracker = _make_tracker()
    host = _make_host()

    with patch("cog.core.workflow.StageExecutor._run_stage", fake_run_stage):
        wf = RalphWorkflow(runner=ScriptedFinalMessageRunner(""), tracker=tracker, host=host)
        await wf._handle_ci_failure(ctx, [], _make_pr(), _failed_checks("test_x"))

    # Should abandon immediately without pushing
    host.push_branch.assert_not_called()
    tracker.add_label.assert_called()


@pytest.mark.asyncio
async def test_unreproducible_comments_on_pr_with_claude_analysis(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COG_CI_MAX_RETRIES", "1")

    async def fake_run_stage(self: object, stage: object, ctx_arg: object) -> object:
        return make_stage_result(
            getattr(stage, "name", "ci-fix"),
            commits=0,
            final_message="I tried running the tests but couldn't reproduce the failure.",
        )

    ctx = _make_ctx(tmp_path)
    host = _make_host()

    with patch("cog.core.workflow.StageExecutor._run_stage", fake_run_stage):
        wf = RalphWorkflow(
            runner=ScriptedFinalMessageRunner(""), tracker=_make_tracker(), host=host
        )
        await wf._handle_ci_failure(ctx, [], _make_pr(), _failed_checks("test_x"))

    pr_comment_body = host.comment_on_pr.call_args[0][1]
    assert "couldn't reproduce" in pr_comment_body or "analysis" in pr_comment_body.lower()


@pytest.mark.asyncio
async def test_unreproducible_adds_agent_failed_label(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COG_CI_MAX_RETRIES", "1")

    async def fake_run_stage(self: object, stage: object, ctx_arg: object) -> object:
        return make_stage_result(getattr(stage, "name", "ci-fix"), commits=0)

    ctx = _make_ctx(tmp_path)
    tracker = _make_tracker()

    with patch("cog.core.workflow.StageExecutor._run_stage", fake_run_stage):
        wf = RalphWorkflow(
            runner=ScriptedFinalMessageRunner(""), tracker=tracker, host=_make_host()
        )
        await wf._handle_ci_failure(ctx, [], _make_pr(), _failed_checks("test_x"))

    add_calls = [call[0][1] for call in tracker.add_label.call_args_list]
    assert "agent-failed" in add_calls


@pytest.mark.asyncio
async def test_unreproducible_removes_agent_ready(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COG_CI_MAX_RETRIES", "1")

    async def fake_run_stage(self: object, stage: object, ctx_arg: object) -> object:
        return make_stage_result(getattr(stage, "name", "ci-fix"), commits=0)

    ctx = _make_ctx(tmp_path)
    tracker = _make_tracker()

    with patch("cog.core.workflow.StageExecutor._run_stage", fake_run_stage):
        wf = RalphWorkflow(
            runner=ScriptedFinalMessageRunner(""), tracker=tracker, host=_make_host()
        )
        await wf._handle_ci_failure(ctx, [], _make_pr(), _failed_checks("test_x"))

    remove_calls = [call[0][1] for call in tracker.remove_label.call_args_list]
    assert "agent-ready" in remove_calls


@pytest.mark.asyncio
async def test_unreproducible_marks_processed_ci_failed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COG_CI_MAX_RETRIES", "1")

    async def fake_run_stage(self: object, stage: object, ctx_arg: object) -> object:
        return make_stage_result(getattr(stage, "name", "ci-fix"), commits=0)

    ctx = _make_ctx(tmp_path)

    with patch("cog.core.workflow.StageExecutor._run_stage", fake_run_stage):
        wf = RalphWorkflow(
            runner=ScriptedFinalMessageRunner(""), tracker=_make_tracker(), host=_make_host()
        )
        await wf._handle_ci_failure(ctx, [], _make_pr(), _failed_checks("test_x"))

    assert ctx.state_cache.is_processed(make_item(item_id="42"))


@pytest.mark.asyncio
async def test_unreproducible_writes_telemetry_with_ci_fix_failed_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COG_CI_MAX_RETRIES", "1")

    async def fake_run_stage(self: object, stage: object, ctx_arg: object) -> object:
        return make_stage_result(getattr(stage, "name", "ci-fix"), commits=0)

    tel = _make_telemetry()
    ctx = _make_ctx(tmp_path, telemetry=tel)

    with patch("cog.core.workflow.StageExecutor._run_stage", fake_run_stage):
        wf = RalphWorkflow(
            runner=ScriptedFinalMessageRunner(""), tracker=_make_tracker(), host=_make_host()
        )
        await wf._handle_ci_failure(ctx, [], _make_pr(), _failed_checks("test_x"))

    tel.write.assert_called_once()
    record = tel.write.call_args[0][0]
    assert record.cause_class == "CiFixFailedError"


# ---------------------------------------------------------------------------
# Cap-exhausted smart comment
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cap_exhausted_comment_flags_same_check_repeated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COG_CI_MAX_RETRIES", "1")
    monkeypatch.setenv("COG_CI_POLL_INTERVAL_SECONDS", "0.01")

    async def fake_run_stage(self: object, stage: object, ctx_arg: object) -> object:
        return make_stage_result(getattr(stage, "name", "ci-fix"), commits=1)

    host = _make_host(checks_sequence=[_failed_checks("test_x")])
    ctx = _make_ctx(tmp_path)

    with patch("cog.core.workflow.StageExecutor._run_stage", fake_run_stage):
        wf = RalphWorkflow(
            runner=ScriptedFinalMessageRunner(""), tracker=_make_tracker(), host=host
        )
        await wf._handle_ci_failure(ctx, [], _make_pr(), _failed_checks("test_x"))

    pr_comment = host.comment_on_pr.call_args[0][1]
    assert "flaky or environment-specific" in pr_comment or "test_x" in pr_comment


@pytest.mark.asyncio
async def test_cap_exhausted_comment_flags_different_checks_as_regression_risk(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COG_CI_MAX_RETRIES", "2")
    monkeypatch.setenv("COG_CI_POLL_INTERVAL_SECONDS", "0.01")

    async def fake_run_stage(self: object, stage: object, ctx_arg: object) -> object:
        return make_stage_result(getattr(stage, "name", "ci-fix"), commits=1)

    # First retry CI fails on test_y, second retry CI fails on test_z
    host = _make_host(checks_sequence=[_failed_checks("test_y"), _failed_checks("test_z")])
    ctx = _make_ctx(tmp_path)

    with patch("cog.core.workflow.StageExecutor._run_stage", fake_run_stage):
        wf = RalphWorkflow(
            runner=ScriptedFinalMessageRunner(""), tracker=_make_tracker(), host=host
        )
        await wf._handle_ci_failure(ctx, [], _make_pr(), _failed_checks("test_x"))

    pr_comment = host.comment_on_pr.call_args[0][1]
    assert "regressions" in pr_comment or "different" in pr_comment.lower()


# ---------------------------------------------------------------------------
# Successful retry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_successful_retry_marks_ci_success_not_ci_failed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COG_CI_MAX_RETRIES", "1")
    monkeypatch.setenv("COG_CI_POLL_INTERVAL_SECONDS", "0.01")

    async def fake_run_stage(self: object, stage: object, ctx_arg: object) -> object:
        return make_stage_result(getattr(stage, "name", "ci-fix"), commits=1)

    host = _make_host(checks_sequence=[_passed_checks("ci")])
    ctx = _make_ctx(tmp_path)
    tracker = _make_tracker()

    with patch("cog.core.workflow.StageExecutor._run_stage", fake_run_stage):
        wf = RalphWorkflow(runner=ScriptedFinalMessageRunner(""), tracker=tracker, host=host)
        await wf._handle_ci_failure(ctx, [], _make_pr(), _failed_checks("test_x"))

    assert ctx.state_cache.is_processed(make_item(item_id="42", title="Fix the bug"))
    # Should be "success" outcome
    remove_calls = [call[0][1] for call in tracker.remove_label.call_args_list]
    assert "agent-ready" in remove_calls


@pytest.mark.asyncio
async def test_successful_retry_records_retry_count_in_telemetry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COG_CI_MAX_RETRIES", "2")
    monkeypatch.setenv("COG_CI_POLL_INTERVAL_SECONDS", "0.01")

    # First fix CI fails again, second fix CI passes
    host = _make_host(checks_sequence=[_failed_checks("test_x"), _passed_checks("ci")])

    async def fake_run_stage(self: object, stage: object, ctx_arg: object) -> object:
        return make_stage_result(getattr(stage, "name", "ci-fix"), commits=1)

    tel = _make_telemetry()
    ctx = _make_ctx(tmp_path, telemetry=tel)

    with patch("cog.core.workflow.StageExecutor._run_stage", fake_run_stage):
        wf = RalphWorkflow(
            runner=ScriptedFinalMessageRunner(""), tracker=_make_tracker(), host=host
        )
        await wf._handle_ci_failure(ctx, [], _make_pr(), _failed_checks("test_x"))

    tel.write.assert_called_once()
    record = tel.write.call_args[0][0]
    assert record.retry_count == 2


@pytest.mark.asyncio
async def test_successful_retry_records_ci_failed_checks_in_telemetry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COG_CI_MAX_RETRIES", "2")
    monkeypatch.setenv("COG_CI_POLL_INTERVAL_SECONDS", "0.01")

    host = _make_host(checks_sequence=[_failed_checks("test_y"), _passed_checks("ci")])

    async def fake_run_stage(self: object, stage: object, ctx_arg: object) -> object:
        return make_stage_result(getattr(stage, "name", "ci-fix"), commits=1)

    tel = _make_telemetry()
    ctx = _make_ctx(tmp_path, telemetry=tel)

    with patch("cog.core.workflow.StageExecutor._run_stage", fake_run_stage):
        wf = RalphWorkflow(
            runner=ScriptedFinalMessageRunner(""), tracker=_make_tracker(), host=host
        )
        await wf._handle_ci_failure(ctx, [], _make_pr(), _failed_checks("test_x"))

    record = tel.write.call_args[0][0]
    assert "test_x" in record.ci_failed_checks
    assert "test_y" in record.ci_failed_checks


# ---------------------------------------------------------------------------
# Telemetry field tests
# ---------------------------------------------------------------------------


def test_telemetry_retry_count_defaults_to_zero_for_no_retries(tmp_path: Path) -> None:
    from cog.telemetry import TelemetryRecord

    record = TelemetryRecord.build(
        project="p",
        workflow="w",
        item=make_item(),
        outcome="success",
        results=[],
        duration_seconds=1.0,
    )
    assert record.retry_count == 0


def test_telemetry_ci_failed_checks_defaults_empty(tmp_path: Path) -> None:
    from cog.telemetry import TelemetryRecord

    record = TelemetryRecord.build(
        project="p",
        workflow="w",
        item=make_item(),
        outcome="success",
        results=[],
        duration_seconds=1.0,
    )
    assert record.ci_failed_checks == ()


def test_telemetry_ci_failed_checks_dedupes_across_retries() -> None:
    history = [(0, ("test_x", "lint")), (1, ("test_x", "test_y"))]
    result = _dedupe_attempt_checks(history)
    # test_x only appears once
    assert result.count("test_x") == 1
    assert "lint" in result
    assert "test_y" in result


def test_telemetry_ci_failed_checks_empty_when_no_failures() -> None:
    history: _AttemptHistory = [(0, ())]
    result = _dedupe_attempt_checks(history)
    assert result == ()


def test_telemetry_record_json_round_trip_preserves_new_fields() -> None:
    import dataclasses
    import json

    from cog.telemetry import TelemetryRecord

    record = TelemetryRecord.build(
        project="p",
        workflow="w",
        item=make_item(),
        outcome="ci-failed",
        results=[],
        duration_seconds=1.0,
        retry_count=2,
        ci_failed_checks=("test_x", "lint"),
    )
    d = dataclasses.asdict(record)
    parsed = json.loads(json.dumps(d))
    assert parsed["retry_count"] == 2
    assert parsed["ci_failed_checks"] == ["test_x", "lint"]


def test_telemetry_record_retry_count_field_defaults_zero() -> None:
    import dataclasses

    from cog.telemetry import TelemetryRecord

    record = TelemetryRecord.build(
        project="p",
        workflow="w",
        item=make_item(),
        outcome="success",
        results=[],
        duration_seconds=1.0,
    )
    d = dataclasses.asdict(record)
    assert d["retry_count"] == 0


def test_telemetry_record_ci_failed_checks_field_defaults_empty() -> None:
    import dataclasses

    from cog.telemetry import TelemetryRecord

    record = TelemetryRecord.build(
        project="p",
        workflow="w",
        item=make_item(),
        outcome="success",
        results=[],
        duration_seconds=1.0,
    )
    d = dataclasses.asdict(record)
    assert d["ci_failed_checks"] == ()


# ---------------------------------------------------------------------------
# Prompt tests
# ---------------------------------------------------------------------------


def test_ci_fix_prompt_exists_and_loads() -> None:
    from cog.workflows.ralph import _load_prompt

    text = _load_prompt("ci_fix")
    assert len(text) > 0


def test_ci_fix_prompt_instructs_claude_to_fetch_logs_via_gh() -> None:
    from cog.workflows.ralph import _load_prompt

    content = _load_prompt("ci_fix")
    assert "gh run view" in content
    assert "--log-failed" in content


def test_ci_fix_prompt_defines_commit_or_exit_contract() -> None:
    from cog.workflows.ralph import _load_prompt

    content = _load_prompt("ci_fix")
    assert "commit" in content.lower()
    assert "exit" in content.lower()


def test_ci_fix_prompt_does_not_inject_log_content() -> None:
    """Regression guard: prompt must not have pre-injected log placeholders."""
    from cog.workflows.ralph import _load_prompt

    content = _load_prompt("ci_fix")
    assert "{ci_log}" not in content
    assert "{log_output}" not in content
    assert "{{" not in content


# Type alias reference for tests
from cog.workflows.ralph import _AttemptHistory  # noqa: E402
