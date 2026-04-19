"""Tests for RalphWorkflow.pre_stages."""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from cog.core.context import ExecutionContext
from cog.core.errors import GitError
from cog.core.tracker import IssueTracker
from cog.workflows.ralph import RalphWorkflow
from tests.fakes import InMemoryStateCache, make_item


def _make_workflow() -> RalphWorkflow:
    return RalphWorkflow(runner=AsyncMock(), tracker=AsyncMock(spec=IssueTracker))


def _make_ctx(item=None) -> ExecutionContext:
    ctx = ExecutionContext(
        project_dir=Path("/fake/project"),
        tmp_dir=Path("/tmp"),
        state_cache=InMemoryStateCache(),
        headless=True,
    )
    ctx.item = item
    return ctx


async def test_pre_stages_git_operations_in_order() -> None:
    item = make_item(item_id="42", title="Fix the bug")
    ctx = _make_ctx(item)
    wf = _make_workflow()
    call_order: list[str] = []

    async def _default_branch(project_dir):
        call_order.append("default_branch")
        return "main"

    async def _checkout(project_dir, branch):
        call_order.append(f"checkout:{branch}")

    async def _fetch(project_dir):
        call_order.append("fetch")

    async def _merge(project_dir, ref):
        call_order.append(f"merge:{ref}")

    async def _create(project_dir, name, start_point="HEAD"):
        call_order.append(f"create:{name}")

    with (
        patch("cog.workflows.ralph.git.default_branch", new=_default_branch),
        patch("cog.workflows.ralph.git.checkout_branch", new=_checkout),
        patch("cog.workflows.ralph.git.fetch_origin", new=_fetch),
        patch("cog.workflows.ralph.git.merge_ff_only", new=_merge),
        patch("cog.workflows.ralph.git.branch_exists", AsyncMock(return_value=False)),
        patch("cog.workflows.ralph.git.create_branch", new=_create),
    ):
        await wf.pre_stages(ctx)

    assert call_order == [
        "default_branch",
        "checkout:main",
        "fetch",
        "merge:origin/main",
        "create:cog/42-fix-the-bug",
    ]


async def test_pre_stages_sets_ctx_work_branch() -> None:
    item = make_item(item_id="7", title="Add feature")
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

    assert ctx.work_branch == "cog/7-add-feature"


async def test_pre_stages_branch_name_format() -> None:
    item = make_item(item_id="99", title="Update deps")
    ctx = _make_ctx(item)
    wf = _make_workflow()

    with (
        patch("cog.workflows.ralph.git.default_branch", AsyncMock(return_value="main")),
        patch("cog.workflows.ralph.git.checkout_branch", AsyncMock()),
        patch("cog.workflows.ralph.git.fetch_origin", AsyncMock()),
        patch("cog.workflows.ralph.git.merge_ff_only", AsyncMock()),
        patch("cog.workflows.ralph.git.branch_exists", AsyncMock(return_value=False)),
        patch("cog.workflows.ralph.git.create_branch", AsyncMock()) as mock_create,
    ):
        await wf.pre_stages(ctx)
        name_arg = mock_create.call_args[0][1]

    assert name_arg.startswith("cog/99-")
    assert name_arg == "cog/99-update-deps"


async def test_pre_stages_propagates_git_error_from_fetch() -> None:
    item = make_item(item_id="1", title="t")
    ctx = _make_ctx(item)
    wf = _make_workflow()

    with (
        patch("cog.workflows.ralph.git.default_branch", AsyncMock(return_value="main")),
        patch("cog.workflows.ralph.git.checkout_branch", AsyncMock()),
        patch(
            "cog.workflows.ralph.git.fetch_origin",
            AsyncMock(side_effect=GitError("fetch failed")),
        ),
        patch("cog.workflows.ralph.git.merge_ff_only", AsyncMock()),
        patch("cog.workflows.ralph.git.branch_exists", AsyncMock(return_value=False)),
        patch("cog.workflows.ralph.git.create_branch", AsyncMock()),
    ):
        with pytest.raises(GitError, match="fetch failed"):
            await wf.pre_stages(ctx)


async def test_pre_stages_propagates_git_error_from_merge() -> None:
    item = make_item(item_id="1", title="t")
    ctx = _make_ctx(item)
    wf = _make_workflow()

    with (
        patch("cog.workflows.ralph.git.default_branch", AsyncMock(return_value="main")),
        patch("cog.workflows.ralph.git.checkout_branch", AsyncMock()),
        patch("cog.workflows.ralph.git.fetch_origin", AsyncMock()),
        patch("cog.workflows.ralph.git.merge_ff_only", AsyncMock(side_effect=GitError("not ff"))),
        patch("cog.workflows.ralph.git.branch_exists", AsyncMock(return_value=False)),
        patch("cog.workflows.ralph.git.create_branch", AsyncMock()),
    ):
        with pytest.raises(GitError, match="not ff"):
            await wf.pre_stages(ctx)


async def test_pre_stages_propagates_git_error_from_create_branch() -> None:
    item = make_item(item_id="1", title="t")
    ctx = _make_ctx(item)
    wf = _make_workflow()

    with (
        patch("cog.workflows.ralph.git.default_branch", AsyncMock(return_value="main")),
        patch("cog.workflows.ralph.git.checkout_branch", AsyncMock()),
        patch("cog.workflows.ralph.git.fetch_origin", AsyncMock()),
        patch("cog.workflows.ralph.git.merge_ff_only", AsyncMock()),
        patch("cog.workflows.ralph.git.branch_exists", AsyncMock(return_value=False)),
        patch(
            "cog.workflows.ralph.git.create_branch",
            AsyncMock(side_effect=GitError("branch already exists")),
        ),
    ):
        with pytest.raises(GitError, match="branch already exists"):
            await wf.pre_stages(ctx)


async def test_pre_stages_asserts_item_is_set() -> None:
    ctx = _make_ctx(item=None)
    wf = _make_workflow()
    with pytest.raises(AssertionError, match="requires ctx.item"):
        await wf.pre_stages(ctx)
