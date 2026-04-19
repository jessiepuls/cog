"""Integration tests for RalphWorkflow."""

import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from cog.core.context import ExecutionContext
from cog.core.host import GitHost
from cog.core.stage import Stage
from cog.core.tracker import IssueTracker
from cog.core.workflow import StageExecutor
from cog.workflows.ralph import RalphWorkflow
from tests.fakes import EchoRunner, InMemoryStateCache, make_item


@pytest.fixture(autouse=True)
def _writable_state_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point XDG_STATE_HOME at a writable temp dir so write_report doesn't fail."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))


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

    tracker_mock = AsyncMock(spec=IssueTracker)
    tracker_mock.list_by_label = AsyncMock(return_value=[item])
    tracker_mock.get = AsyncMock(return_value=item)
    host_mock = AsyncMock(spec=GitHost)

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
