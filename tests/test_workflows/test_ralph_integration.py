"""Integration tests for RalphWorkflow with a real git repo."""

import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from cog.core.context import ExecutionContext
from cog.core.tracker import IssueTracker
from cog.core.workflow import StageExecutor
from cog.workflows.ralph import RalphWorkflow
from tests.fakes import EchoRunner, InMemoryStateCache, make_item


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


@pytest.mark.skipif(shutil.which("git") is None, reason="git not available")
async def test_end_to_end_with_real_git(git_env: Path) -> None:
    item = make_item(
        tracker_id="github/test/repo",
        item_id="42",
        title="Fix the bug",
        created_at=datetime(2024, 1, 1, tzinfo=UTC),
    )

    tracker_mock = AsyncMock(spec=IssueTracker)
    tracker_mock.list_by_label = AsyncMock(return_value=[item])

    wf = RalphWorkflow(runner=EchoRunner(), tracker=tracker_mock)

    # Monkey-patch stages so we don't hit NotImplementedError
    from cog.core.stage import Stage

    def _stages(ctx):
        return [Stage(name="build", prompt_source=lambda _: "go", model="m", runner=EchoRunner())]

    async def _classify(ctx, results):
        return "success"

    wf.stages = _stages  # type: ignore[method-assign]
    wf.classify_outcome = _classify  # type: ignore[method-assign]

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
