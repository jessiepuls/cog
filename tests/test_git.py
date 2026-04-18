"""Tests for cog.git async subprocess helpers."""

from pathlib import Path
from unittest.mock import patch

import pytest

from cog.core.errors import GitError
from cog.git import (
    checkout_branch,
    commits_between,
    create_branch,
    current_branch,
    current_head_sha,
    default_branch,
    fetch_origin,
    merge_ff_only,
)
from tests.fakes import FakeSubprocessRegistry


def _patch_exec(registry: FakeSubprocessRegistry):
    return patch("asyncio.create_subprocess_exec", new=registry.create_subprocess_exec)


async def test_default_branch_strips_origin_prefix(tmp_path: Path) -> None:
    registry = FakeSubprocessRegistry()
    registry.expect(
        ("git", "symbolic-ref", "--short", "refs/remotes/origin/HEAD"),
        stdout=b"origin/main\n",
    )
    with _patch_exec(registry):
        result = await default_branch(tmp_path)
    assert result == "main"


async def test_default_branch_raises_on_missing(tmp_path: Path) -> None:
    registry = FakeSubprocessRegistry()
    registry.expect(
        ("git", "symbolic-ref", "--short", "refs/remotes/origin/HEAD"),
        returncode=128,
        stderr=b"fatal: ref refs/remotes/origin/HEAD is not a symbolic ref\n",
    )
    with _patch_exec(registry):
        with pytest.raises(GitError):
            await default_branch(tmp_path)


async def test_current_branch_happy(tmp_path: Path) -> None:
    registry = FakeSubprocessRegistry()
    registry.expect(
        ("git", "symbolic-ref", "--short", "HEAD"),
        stdout=b"feature/my-branch\n",
    )
    with _patch_exec(registry):
        result = await current_branch(tmp_path)
    assert result == "feature/my-branch"


async def test_current_branch_raises_on_detached_head(tmp_path: Path) -> None:
    registry = FakeSubprocessRegistry()
    registry.expect(
        ("git", "symbolic-ref", "--short", "HEAD"),
        returncode=128,
        stderr=b"fatal: HEAD is not a symbolic ref\n",
    )
    with _patch_exec(registry):
        with pytest.raises(GitError):
            await current_branch(tmp_path)


async def test_current_head_sha(tmp_path: Path) -> None:
    sha = "abc1234def5678" * 3
    registry = FakeSubprocessRegistry()
    registry.expect(
        ("git", "rev-parse", "HEAD"),
        stdout=f"{sha}\n".encode(),
    )
    with _patch_exec(registry):
        result = await current_head_sha(tmp_path)
    assert result == sha


async def test_commits_between_zero(tmp_path: Path) -> None:
    registry = FakeSubprocessRegistry()
    registry.expect(
        ("git", "rev-list", "--count", "abc123..HEAD"),
        stdout=b"0\n",
    )
    with _patch_exec(registry):
        result = await commits_between(tmp_path, "abc123")
    assert result == 0


async def test_commits_between_nonzero(tmp_path: Path) -> None:
    registry = FakeSubprocessRegistry()
    registry.expect(
        ("git", "rev-list", "--count", "abc123..HEAD"),
        stdout=b"3\n",
    )
    with _patch_exec(registry):
        result = await commits_between(tmp_path, "abc123")
    assert result == 3


async def test_checkout_branch_argv(tmp_path: Path) -> None:
    registry = FakeSubprocessRegistry()
    registry.expect(("git", "checkout", "main"), stdout=b"")
    with _patch_exec(registry):
        await checkout_branch(tmp_path, "main")
    assert ("git", "checkout", "main") in registry.calls


async def test_fetch_origin_argv(tmp_path: Path) -> None:
    registry = FakeSubprocessRegistry()
    registry.expect(("git", "fetch", "origin"), stdout=b"")
    with _patch_exec(registry):
        await fetch_origin(tmp_path)
    assert ("git", "fetch", "origin") in registry.calls


async def test_merge_ff_only_argv(tmp_path: Path) -> None:
    registry = FakeSubprocessRegistry()
    registry.expect(("git", "merge", "--ff-only", "origin/main"), stdout=b"")
    with _patch_exec(registry):
        await merge_ff_only(tmp_path, "origin/main")
    assert ("git", "merge", "--ff-only", "origin/main") in registry.calls


async def test_merge_ff_only_raises_on_nonff(tmp_path: Path) -> None:
    registry = FakeSubprocessRegistry()
    registry.expect(
        ("git", "merge", "--ff-only", "origin/main"),
        returncode=1,
        stderr=b"fatal: Not possible to fast-forward, aborting.\n",
    )
    with _patch_exec(registry):
        with pytest.raises(GitError):
            await merge_ff_only(tmp_path, "origin/main")


async def test_create_branch_argv(tmp_path: Path) -> None:
    registry = FakeSubprocessRegistry()
    registry.expect(("git", "checkout", "-b", "cog/42-my-branch", "HEAD"), stdout=b"")
    with _patch_exec(registry):
        await create_branch(tmp_path, "cog/42-my-branch")
    assert ("git", "checkout", "-b", "cog/42-my-branch", "HEAD") in registry.calls


async def test_create_branch_raises_when_exists(tmp_path: Path) -> None:
    registry = FakeSubprocessRegistry()
    registry.expect(
        ("git", "checkout", "-b", "cog/42-my-branch", "HEAD"),
        returncode=128,
        stderr=b"fatal: A branch named 'cog/42-my-branch' already exists.\n",
    )
    with _patch_exec(registry):
        with pytest.raises(GitError):
            await create_branch(tmp_path, "cog/42-my-branch")
