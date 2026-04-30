"""Tests for RalphWorkflow.iteration_start (was pre_stages)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from cog.core.context import ExecutionContext
from cog.core.errors import GitError
from cog.core.tracker import IssueTracker
from cog.core.workflow import IterationOutcome
from cog.workflows.ralph import RalphWorkflow, _BranchCase, _TeardownAction
from tests.fakes import InMemoryStateCache, make_item


def _make_workflow(*, restart: bool = False) -> RalphWorkflow:
    return RalphWorkflow(runner=AsyncMock(), tracker=AsyncMock(spec=IssueTracker), restart=restart)


def _make_ctx(item=None) -> ExecutionContext:
    ctx = ExecutionContext(
        project_dir=Path("/fake/project"),
        tmp_dir=Path("/tmp"),
        state_cache=InMemoryStateCache(),
        headless=True,
    )
    ctx.item = item
    return ctx


def _worktree_patches(
    *,
    default_branch: str = "main",
    branch_exists: bool = False,
    commits_between: int = 0,
    remote_exists: bool = False,
):
    """Build a context manager that patches all worktree-related functions."""
    from unittest.mock import patch as _patch

    class _PatchGroup:
        def __init__(self):
            self._patches = [
                _patch(
                    "cog.workflows.ralph.git.default_branch", AsyncMock(return_value=default_branch)
                ),
                _patch("cog.workflows.ralph.git.fetch_origin", AsyncMock()),
                _patch(
                    "cog.workflows.ralph.git.branch_exists", AsyncMock(return_value=branch_exists)
                ),
                _patch(
                    "cog.workflows.ralph.git.commits_between",
                    AsyncMock(return_value=commits_between),
                ),
                _patch("cog.workflows.ralph.git.delete_branch", AsyncMock()),
                _patch("cog.workflows.ralph.create_worktree", AsyncMock()),
                _patch(
                    "cog.workflows.ralph.remote_branch_exists",
                    AsyncMock(return_value=remote_exists),
                ),
                _patch("cog.workflows.ralph.discard_worktree", AsyncMock()),
            ]
            self._started: list = []

        def __enter__(self):
            mocks = {}
            for p in self._patches:
                m = p.start()
                mocks[p.attribute] = m
            self._started = self._patches
            return mocks

        def __exit__(self, *args):
            for p in self._started:
                p.stop()

    return _PatchGroup()


# ---------------------------------------------------------------------------
# iteration_start: fresh start (no existing branch)
# ---------------------------------------------------------------------------


async def test_iteration_start_creates_worktree_for_new_branch() -> None:
    item = make_item(item_id="42", title="Fix the bug")
    ctx = _make_ctx(item)
    wf = _make_workflow()
    create_mock = AsyncMock()

    with (
        patch("cog.workflows.ralph.git.default_branch", AsyncMock(return_value="main")),
        patch("cog.workflows.ralph.git.fetch_origin", AsyncMock()),
        patch("cog.workflows.ralph.git.branch_exists", AsyncMock(return_value=False)),
        patch("cog.workflows.ralph.remote_branch_exists", AsyncMock(return_value=False)),
        patch("cog.workflows.ralph.create_worktree", create_mock),
        patch("cog.workflows.ralph.discard_worktree", AsyncMock()),
    ):
        await wf.iteration_start(ctx)

    create_mock.assert_awaited_once()
    call_kwargs = create_mock.call_args
    assert call_kwargs.kwargs["create_branch"] is True
    assert call_kwargs.kwargs["start_point"] == "origin/main"
    assert ctx.work_branch == "cog/42-fix-the-bug"
    assert ctx.resumed is False


async def test_iteration_start_sets_worktree_path() -> None:
    item = make_item(item_id="7", title="Add feature")
    ctx = _make_ctx(item)
    wf = _make_workflow()

    with (
        patch("cog.workflows.ralph.git.default_branch", AsyncMock(return_value="main")),
        patch("cog.workflows.ralph.git.fetch_origin", AsyncMock()),
        patch("cog.workflows.ralph.git.branch_exists", AsyncMock(return_value=False)),
        patch("cog.workflows.ralph.remote_branch_exists", AsyncMock(return_value=False)),
        patch("cog.workflows.ralph.create_worktree", AsyncMock()),
        patch("cog.workflows.ralph.discard_worktree", AsyncMock()),
    ):
        await wf.iteration_start(ctx)

    expected_wt = Path("/fake/project") / ".cog" / "worktrees" / "7-add-feature"
    assert ctx.worktree_path == expected_wt


# ---------------------------------------------------------------------------
# iteration_start: resume cases
# ---------------------------------------------------------------------------


async def test_iteration_start_resumes_local_branch_with_commits() -> None:
    item = make_item(item_id="42", title="Fix the bug")
    ctx = _make_ctx(item)
    wf = _make_workflow()
    create_mock = AsyncMock()

    with (
        patch("cog.workflows.ralph.git.default_branch", AsyncMock(return_value="main")),
        patch("cog.workflows.ralph.git.fetch_origin", AsyncMock()),
        patch("cog.workflows.ralph.git.branch_exists", AsyncMock(return_value=True)),
        patch("cog.workflows.ralph.git.commits_between", AsyncMock(return_value=3)),
        patch("cog.workflows.ralph.create_worktree", create_mock),
        patch("cog.workflows.ralph.discard_worktree", AsyncMock()),
        patch("cog.workflows.ralph.remote_branch_exists", AsyncMock(return_value=False)),
    ):
        await wf.iteration_start(ctx)

    # Should use create_branch=False to attach to existing branch
    call_kwargs = create_mock.call_args
    assert call_kwargs.kwargs["create_branch"] is False
    assert ctx.resumed is True


async def test_iteration_start_deletes_stale_branch_and_creates_fresh() -> None:
    item = make_item(item_id="42", title="Fix the bug")
    ctx = _make_ctx(item)
    wf = _make_workflow()
    delete_mock = AsyncMock()
    create_mock = AsyncMock()

    with (
        patch("cog.workflows.ralph.git.default_branch", AsyncMock(return_value="main")),
        patch("cog.workflows.ralph.git.fetch_origin", AsyncMock()),
        patch("cog.workflows.ralph.git.branch_exists", AsyncMock(return_value=True)),
        patch("cog.workflows.ralph.git.commits_between", AsyncMock(return_value=0)),
        patch("cog.workflows.ralph.git.delete_branch", delete_mock),
        patch("cog.workflows.ralph.create_worktree", create_mock),
        patch("cog.workflows.ralph.discard_worktree", AsyncMock()),
        patch("cog.workflows.ralph.remote_branch_exists", AsyncMock(return_value=False)),
    ):
        await wf.iteration_start(ctx)

    delete_mock.assert_awaited_once()
    assert create_mock.call_args.kwargs["create_branch"] is True
    assert ctx.resumed is False


async def test_iteration_start_resumes_from_origin_when_no_local_branch() -> None:
    item = make_item(item_id="42", title="Fix the bug")
    ctx = _make_ctx(item)
    wf = _make_workflow()
    create_mock = AsyncMock()

    with (
        patch("cog.workflows.ralph.git.default_branch", AsyncMock(return_value="main")),
        patch("cog.workflows.ralph.git.fetch_origin", AsyncMock()),
        patch("cog.workflows.ralph.git.branch_exists", AsyncMock(return_value=False)),
        patch("cog.workflows.ralph.remote_branch_exists", AsyncMock(return_value=True)),
        patch("cog.workflows.ralph.create_worktree", create_mock),
        patch("cog.workflows.ralph.discard_worktree", AsyncMock()),
    ):
        await wf.iteration_start(ctx)

    call_kwargs = create_mock.call_args
    assert "origin/cog/42-fix-the-bug" in call_kwargs.kwargs["start_point"]
    assert ctx.resumed is True


# ---------------------------------------------------------------------------
# iteration_start: restart flag
# ---------------------------------------------------------------------------


async def test_iteration_start_restart_force_cleans_and_creates_fresh() -> None:
    item = make_item(item_id="42", title="Fix the bug")
    ctx = _make_ctx(item)
    wf = _make_workflow(restart=True)
    discard_mock = AsyncMock()
    delete_mock = AsyncMock()
    create_mock = AsyncMock()

    with (
        patch("cog.workflows.ralph.git.default_branch", AsyncMock(return_value="main")),
        patch("cog.workflows.ralph.git.fetch_origin", AsyncMock()),
        patch("cog.workflows.ralph.git.branch_exists", AsyncMock(return_value=True)),
        patch("cog.workflows.ralph.git.delete_branch", delete_mock),
        patch("cog.workflows.ralph.create_worktree", create_mock),
        patch("cog.workflows.ralph.discard_worktree", discard_mock),
        patch("pathlib.Path.exists", return_value=True),
    ):
        await wf.iteration_start(ctx)

    delete_mock.assert_awaited_once()
    assert create_mock.call_args.kwargs["create_branch"] is True
    assert ctx.resumed is False


async def test_iteration_start_asserts_item_is_set() -> None:
    ctx = _make_ctx(item=None)
    wf = _make_workflow()
    with pytest.raises(AssertionError, match="requires ctx.item"):
        await wf.iteration_start(ctx)


# ---------------------------------------------------------------------------
# iteration_start: propagates git errors
# ---------------------------------------------------------------------------


async def test_iteration_start_propagates_git_error_from_fetch() -> None:
    item = make_item(item_id="1", title="t")
    ctx = _make_ctx(item)
    wf = _make_workflow()

    with (
        patch("cog.workflows.ralph.git.default_branch", AsyncMock(return_value="main")),
        patch(
            "cog.workflows.ralph.git.fetch_origin",
            AsyncMock(side_effect=GitError("fetch failed")),
        ),
    ):
        with pytest.raises(GitError, match="fetch failed"):
            await wf.iteration_start(ctx)


# ---------------------------------------------------------------------------
# _classify_branch: one test per case
# ---------------------------------------------------------------------------


async def test_classify_branch_restart() -> None:
    wf = _make_workflow(restart=True)
    ctx = _make_ctx(make_item(item_id="1", title="t"))
    with (
        patch("cog.workflows.ralph.git.branch_exists", AsyncMock(return_value=False)),
        patch("cog.workflows.ralph.remote_branch_exists", AsyncMock(return_value=False)),
        patch("cog.workflows.ralph.git.commits_between", AsyncMock(return_value=0)),
    ):
        result = await wf._classify_branch(ctx, "cog/1-t", "main")
    assert result is _BranchCase.RESTART


async def test_classify_branch_resume_local() -> None:
    wf = _make_workflow()
    ctx = _make_ctx(make_item(item_id="1", title="t"))
    with (
        patch("cog.workflows.ralph.git.branch_exists", AsyncMock(return_value=True)),
        patch("cog.workflows.ralph.git.commits_between", AsyncMock(return_value=2)),
    ):
        result = await wf._classify_branch(ctx, "cog/1-t", "main")
    assert result is _BranchCase.RESUME_LOCAL


async def test_classify_branch_recreate_stale() -> None:
    wf = _make_workflow()
    ctx = _make_ctx(make_item(item_id="1", title="t"))
    with (
        patch("cog.workflows.ralph.git.branch_exists", AsyncMock(return_value=True)),
        patch("cog.workflows.ralph.git.commits_between", AsyncMock(return_value=0)),
    ):
        result = await wf._classify_branch(ctx, "cog/1-t", "main")
    assert result is _BranchCase.RECREATE_STALE


async def test_classify_branch_resume_remote() -> None:
    wf = _make_workflow()
    ctx = _make_ctx(make_item(item_id="1", title="t"))
    with (
        patch("cog.workflows.ralph.git.branch_exists", AsyncMock(return_value=False)),
        patch("cog.workflows.ralph.remote_branch_exists", AsyncMock(return_value=True)),
    ):
        result = await wf._classify_branch(ctx, "cog/1-t", "main")
    assert result is _BranchCase.RESUME_REMOTE


async def test_classify_branch_fresh_start() -> None:
    wf = _make_workflow()
    ctx = _make_ctx(make_item(item_id="1", title="t"))
    with (
        patch("cog.workflows.ralph.git.branch_exists", AsyncMock(return_value=False)),
        patch("cog.workflows.ralph.remote_branch_exists", AsyncMock(return_value=False)),
    ):
        result = await wf._classify_branch(ctx, "cog/1-t", "main")
    assert result is _BranchCase.FRESH_START


# ---------------------------------------------------------------------------
# _teardown_action: matrix of (outcome, dirty, ahead) -> _TeardownAction
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "outcome,dirty,ahead,expected",
    [
        # success: clean → remove; dirty → leave_stuck
        (IterationOutcome.success, False, False, _TeardownAction.REMOVE),
        (IterationOutcome.success, True, False, _TeardownAction.LEAVE_STUCK),
        # noop: clean+no-ahead → remove; clean+ahead → push_then_remove; dirty → leave_stuck
        (IterationOutcome.noop, False, False, _TeardownAction.REMOVE),
        (IterationOutcome.noop, False, True, _TeardownAction.PUSH_THEN_REMOVE_OR_STUCK),
        (IterationOutcome.noop, True, True, _TeardownAction.LEAVE_STUCK),
        # error: clean+no-ahead → remove; clean+ahead → push_then_remove; dirty+ahead → best_effort
        (IterationOutcome.error, False, False, _TeardownAction.REMOVE),
        (IterationOutcome.error, False, True, _TeardownAction.PUSH_THEN_REMOVE_OR_STUCK),
        (IterationOutcome.error, True, True, _TeardownAction.PUSH_BEST_EFFORT_THEN_STUCK),
    ],
)
async def test_teardown_action_matrix(
    outcome: IterationOutcome,
    dirty: bool,
    ahead: bool,
    expected: _TeardownAction,
    tmp_path: Path,
) -> None:
    wf = _make_workflow()
    with (
        patch("cog.workflows.ralph.is_dirty", AsyncMock(return_value=dirty)),
        patch("cog.workflows.ralph.is_ahead_of_origin", AsyncMock(return_value=ahead)),
    ):
        result = await wf._teardown_action(tmp_path, "cog/1-t", outcome)
    assert result is expected
