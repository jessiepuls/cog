"""Integration tests for RalphWorkflow."""

import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from cog.core.context import ExecutionContext
from cog.core.host import GitHost, PullRequest
from cog.core.stage import Stage
from cog.core.tracker import IssueTracker
from cog.core.workflow import StageExecutor
from cog.workflows.ralph import RalphWorkflow
from tests.fakes import EchoRunner, InMemoryStateCache, ScriptedFinalMessageRunner, make_item


@pytest.fixture(autouse=True)
def _writable_state_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point XDG_STATE_HOME at a writable temp dir so write_report doesn't fail."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))


@pytest.fixture(autouse=True)
def _clean_rebase(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch _rebase_before_push to return clean so integration tests don't hit git."""
    from cog.workflows.ralph import RebaseOutcome

    async def _noop(self: object, ctx: object) -> RebaseOutcome:
        return RebaseOutcome(status="clean")

    monkeypatch.setattr(RalphWorkflow, "_rebase_before_push", _noop)


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def git_env(tmp_path: Path):
    """Set up a local repo with origin (bare clone)."""
    origin = tmp_path / "origin.git"
    repo = tmp_path / "repo"

    origin.mkdir()
    _git("init", "--bare", str(origin), cwd=tmp_path)

    repo.mkdir()
    _git("init", cwd=repo)
    _git("config", "user.email", "test@test.com", cwd=repo)
    _git("config", "user.name", "Test", cwd=repo)
    _git("remote", "add", "origin", str(origin), cwd=repo)

    (repo / "README.md").write_text("hello")
    _git("add", ".", cwd=repo)
    _git("commit", "-m", "initial commit", cwd=repo)
    _git("branch", "-M", "main", cwd=repo)
    _git("push", "-u", "origin", "main", cwd=repo)

    # Set the symbolic ref so default_branch() works
    _git("remote", "set-head", "origin", "main", cwd=repo)

    return repo


async def test_runs_all_three_stages_end_to_end(tmp_path):
    """Verify the 3-stage sequence runs via pre-selected item (no git ops)."""
    tracker_mock = AsyncMock(spec=IssueTracker)
    runner = EchoRunner()
    wf = RalphWorkflow(runner, tracker_mock)

    # Short-circuit pre_stages; this test isolates stage-sequence behavior
    # from git-setup concerns (which are covered by test_end_to_end_with_real_git).
    async def _noop_pre_stages(ctx):
        return

    wf.pre_stages = _noop_pre_stages  # type: ignore[method-assign]

    ctx = ExecutionContext(
        project_dir=tmp_path,
        tmp_dir=tmp_path,
        state_cache=InMemoryStateCache(),
        headless=True,
        item=make_item(),  # pre-select item, bypassing select_item
        work_branch="ralph/42-test",
    )

    results = await StageExecutor().run(wf, ctx)

    assert len(results) == 3
    assert [r.stage.name for r in results] == ["build", "review", "document"]
    assert all(r.exit_status == 0 for r in results)
    assert all(r.error is None for r in results)


@pytest.mark.skipif(shutil.which("git") is None, reason="git not available")
async def test_end_to_end_with_real_git(git_env: Path) -> None:
    """Verify pre_stages creates a real git branch via cog.git helpers."""
    item = make_item(
        tracker_id="github/test/repo",
        item_id="42",
        title="Fix the bug",
        created_at=datetime(2024, 1, 1, tzinfo=UTC),
    )

    from cog.core.host import PrChecks

    tracker_mock = AsyncMock(spec=IssueTracker)
    tracker_mock.list_by_label = AsyncMock(return_value=[item])
    tracker_mock.get = AsyncMock(return_value=item)
    host_mock = AsyncMock(spec=GitHost)
    host_mock.get_pr_checks.return_value = PrChecks(runs=())

    wf = RalphWorkflow(runner=EchoRunner(), tracker=tracker_mock, host=host_mock)

    # Monkey-patch stages to a single stage so this test focuses on pre_stages
    # behavior (branch creation) rather than the full build/review/document cycle.
    def _stages(ctx):
        return [Stage(name="build", prompt_source=lambda _: "go", model="m", runner=EchoRunner())]

    async def _classify(ctx, results):
        return "success"

    wf.stages = _stages  # type: ignore[method-assign]
    wf.classify_outcome = _classify  # type: ignore[method-assign]
    wf.write_report = AsyncMock()  # type: ignore[method-assign]

    ctx = ExecutionContext(
        project_dir=git_env,
        tmp_dir=git_env / "tmp",
        state_cache=InMemoryStateCache(),
        headless=True,
    )

    results = await StageExecutor().run(wf, ctx)

    # pre_stages should have set work_branch
    assert ctx.work_branch is not None
    assert ctx.work_branch.startswith("cog/42-")

    # Branch should exist on disk
    completed = subprocess.run(
        ["git", "branch", "--list", ctx.work_branch],
        cwd=git_env,
        capture_output=True,
        text=True,
    )
    assert ctx.work_branch in completed.stdout

    # Stage ran
    assert len(results) == 1


# ---------------------------------------------------------------------------
# PR body with structured / unstructured final message
# ---------------------------------------------------------------------------

_FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "ralph"


def _make_pr(number: int = 1, url: str = "https://github.com/org/repo/pull/1") -> PullRequest:
    return PullRequest(
        number=number,
        url=url,
        state="open",
        body="",
        head_branch="cog/1-test-item",
    )


async def test_finalize_success_pr_body_with_structured_final_message(tmp_path: Path) -> None:
    """ScriptedFinalMessageRunner emitting structured output → all three sections in PR body."""
    from cog.core.host import PrChecks as _PrChecks

    final_message = (_FIXTURE_DIR / "final_message_structured.md").read_text()

    tracker = AsyncMock(spec=IssueTracker)
    host = AsyncMock(spec=GitHost)
    host.push_branch.return_value = None
    host.get_pr_for_branch.return_value = None
    host.get_pr_checks.return_value = _PrChecks(runs=())
    pr = _make_pr()
    host.create_pr.return_value = pr

    wf = RalphWorkflow(
        runner=ScriptedFinalMessageRunner(final_message, cost=0.05),
        tracker=tracker,
        host=host,
    )

    async def _noop_pre_stages(ctx: ExecutionContext) -> None:
        return

    wf.pre_stages = _noop_pre_stages  # type: ignore[method-assign]

    ctx = ExecutionContext(
        project_dir=tmp_path,
        tmp_dir=tmp_path,
        state_cache=InMemoryStateCache(),
        headless=True,
        item=make_item(item_id="99", title="Structured test"),
        work_branch="cog/99-structured-test",
    )

    results = await StageExecutor().run(wf, ctx)
    await wf.finalize_success(ctx, results)

    body = host.create_pr.call_args.kwargs["body"]
    assert "## Summary" in body
    assert "Adds `--max-iterations`" in body
    assert "## Key changes" in body
    assert "src/cog/loop.py" in body
    assert "## Test plan" in body
    assert "- [ ] Run `cog ralph --loop --max-iterations 2`" in body
    assert "## Closes" in body
    assert "Closes #99" in body


async def test_finalize_success_pr_body_with_unstructured_final_message(tmp_path: Path) -> None:
    """Terse final message → Summary = full message, no Key changes, test-plan fallback."""
    from cog.core.host import PrChecks as _PrChecks

    final_message = (_FIXTURE_DIR / "final_message_terse.md").read_text().strip()

    tracker = AsyncMock(spec=IssueTracker)
    host = AsyncMock(spec=GitHost)
    host.push_branch.return_value = None
    host.get_pr_for_branch.return_value = None
    host.get_pr_checks.return_value = _PrChecks(runs=())
    host.create_pr.return_value = _make_pr()

    wf = RalphWorkflow(
        runner=ScriptedFinalMessageRunner(final_message, cost=0.01),
        tracker=tracker,
        host=host,
    )

    async def _noop_pre_stages(ctx: ExecutionContext) -> None:
        return

    wf.pre_stages = _noop_pre_stages  # type: ignore[method-assign]

    ctx = ExecutionContext(
        project_dir=tmp_path,
        tmp_dir=tmp_path,
        state_cache=InMemoryStateCache(),
        headless=True,
        item=make_item(item_id="41", title="Terse test"),
        work_branch="cog/41-terse-test",
    )

    results = await StageExecutor().run(wf, ctx)
    await wf.finalize_success(ctx, results)

    body = host.create_pr.call_args.kwargs["body"]
    assert "## Summary" in body
    assert "Committed. All tests and linters pass." in body
    assert "## Key changes" not in body
    assert "## Test plan" in body
    assert "Manual verification" in body
    assert "Closes #41" in body
