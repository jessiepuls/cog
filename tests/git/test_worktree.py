"""Tests for cog.git.worktree module against real git repos."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from cog.core.errors import GitError
from cog.git.worktree import (
    create_worktree,
    discard_worktree,
    is_ahead_of_origin,
    is_dirty,
    list_worktrees,
    prune,
    push_branch,
    push_with_retry,
    remove_worktree,
    scan_orphans,
)

# ---------------------------------------------------------------------------
# create_worktree / remove_worktree
# ---------------------------------------------------------------------------


async def test_create_worktree_new_branch(temp_git_repo: Path) -> None:
    default = subprocess.run(
        ["git", "-C", str(temp_git_repo), "symbolic-ref", "--short", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    wt_path = temp_git_repo.parent / "wt-new"
    await create_worktree(
        temp_git_repo,
        wt_path,
        "cog/1-test",
        start_point=f"origin/{default}",
        create_branch=True,
    )
    assert wt_path.is_dir()
    assert (wt_path / ".git").exists()
    current = subprocess.run(
        ["git", "-C", str(wt_path), "symbolic-ref", "--short", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert current == "cog/1-test"


async def test_create_worktree_existing_branch(temp_git_repo: Path) -> None:
    default = subprocess.run(
        ["git", "-C", str(temp_git_repo), "symbolic-ref", "--short", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    # Create a branch first
    subprocess.run(
        ["git", "-C", str(temp_git_repo), "branch", "cog/2-existing", f"origin/{default}"],
        capture_output=True,
        check=True,
    )
    wt_path = temp_git_repo.parent / "wt-existing"
    await create_worktree(
        temp_git_repo,
        wt_path,
        "cog/2-existing",
        start_point="cog/2-existing",
        create_branch=False,
    )
    assert wt_path.is_dir()
    current = subprocess.run(
        ["git", "-C", str(wt_path), "symbolic-ref", "--short", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert current == "cog/2-existing"


async def test_remove_worktree(temp_git_repo: Path) -> None:
    default = subprocess.run(
        ["git", "-C", str(temp_git_repo), "symbolic-ref", "--short", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    wt_path = temp_git_repo.parent / "wt-remove"
    await create_worktree(
        temp_git_repo,
        wt_path,
        "cog/3-remove",
        start_point=f"origin/{default}",
        create_branch=True,
    )
    assert wt_path.is_dir()
    await remove_worktree(temp_git_repo, wt_path)
    assert not wt_path.exists()


async def test_remove_worktree_raises_on_nonexistent(temp_git_repo: Path) -> None:
    with pytest.raises(GitError):
        await remove_worktree(temp_git_repo, temp_git_repo.parent / "does-not-exist")


# ---------------------------------------------------------------------------
# list_worktrees
# ---------------------------------------------------------------------------


async def test_list_worktrees_includes_main(temp_git_repo: Path) -> None:
    worktrees = await list_worktrees(temp_git_repo)
    assert len(worktrees) >= 1
    paths = [wt.path for wt in worktrees]
    assert temp_git_repo in paths


async def test_list_worktrees_includes_added(temp_git_repo: Path) -> None:
    default = subprocess.run(
        ["git", "-C", str(temp_git_repo), "symbolic-ref", "--short", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    wt_path = temp_git_repo.parent / "wt-list"
    await create_worktree(
        temp_git_repo,
        wt_path,
        "cog/4-list",
        start_point=f"origin/{default}",
        create_branch=True,
    )
    worktrees = await list_worktrees(temp_git_repo)
    paths = [wt.path for wt in worktrees]
    assert wt_path in paths
    await remove_worktree(temp_git_repo, wt_path)


# ---------------------------------------------------------------------------
# prune
# ---------------------------------------------------------------------------


async def test_prune_succeeds(temp_git_repo: Path) -> None:
    # Should not raise even if nothing to prune
    await prune(temp_git_repo)


# ---------------------------------------------------------------------------
# is_dirty
# ---------------------------------------------------------------------------


async def test_is_dirty_clean_repo(temp_git_repo: Path) -> None:
    assert await is_dirty(temp_git_repo) is False


async def test_is_dirty_with_untracked(temp_git_repo: Path) -> None:
    (temp_git_repo / "new_file.txt").write_text("untracked")
    assert await is_dirty(temp_git_repo) is True


async def test_is_dirty_with_modified(temp_git_repo: Path) -> None:
    (temp_git_repo / "README.md").write_text("modified")
    assert await is_dirty(temp_git_repo) is True


async def test_is_dirty_ignores_gitignored(temp_git_repo: Path) -> None:
    (temp_git_repo / ".gitignore").write_text("ignored.txt\n")
    subprocess.run(
        ["git", "-C", str(temp_git_repo), "add", ".gitignore"],
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(temp_git_repo), "commit", "-m", "add gitignore"],
        capture_output=True,
        check=True,
    )
    (temp_git_repo / "ignored.txt").write_text("this is ignored")
    assert await is_dirty(temp_git_repo) is False


# ---------------------------------------------------------------------------
# is_ahead_of_origin
# ---------------------------------------------------------------------------


async def test_is_ahead_of_origin_false_when_in_sync(temp_git_repo: Path) -> None:
    default = subprocess.run(
        ["git", "-C", str(temp_git_repo), "symbolic-ref", "--short", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert await is_ahead_of_origin(temp_git_repo, default) is False


async def test_is_ahead_of_origin_true_after_commit(temp_git_repo: Path) -> None:
    default = subprocess.run(
        ["git", "-C", str(temp_git_repo), "symbolic-ref", "--short", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    (temp_git_repo / "new.txt").write_text("new")
    subprocess.run(
        ["git", "-C", str(temp_git_repo), "add", "new.txt"],
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(temp_git_repo), "commit", "-m", "new commit"],
        capture_output=True,
        check=True,
    )
    assert await is_ahead_of_origin(temp_git_repo, default) is True


async def test_is_ahead_of_origin_true_when_remote_lacks_branch(temp_git_repo: Path) -> None:
    # Branch that doesn't exist on origin
    default = subprocess.run(
        ["git", "-C", str(temp_git_repo), "symbolic-ref", "--short", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    wt_path = temp_git_repo.parent / "wt-ahead"
    await create_worktree(
        temp_git_repo,
        wt_path,
        "cog/5-ahead",
        start_point=f"origin/{default}",
        create_branch=True,
    )
    assert await is_ahead_of_origin(wt_path, "cog/5-ahead") is True
    await remove_worktree(temp_git_repo, wt_path)


# ---------------------------------------------------------------------------
# push_branch
# ---------------------------------------------------------------------------


async def test_push_branch_succeeds(temp_git_repo: Path) -> None:
    default = subprocess.run(
        ["git", "-C", str(temp_git_repo), "symbolic-ref", "--short", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    wt_path = temp_git_repo.parent / "wt-push"
    await create_worktree(
        temp_git_repo,
        wt_path,
        "cog/6-push",
        start_point=f"origin/{default}",
        create_branch=True,
    )
    # Make a commit in the worktree
    (wt_path / "pushed.txt").write_text("pushed")
    subprocess.run(
        ["git", "-C", str(wt_path), "add", "pushed.txt"],
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(wt_path), "commit", "-m", "push me"],
        capture_output=True,
        check=True,
    )
    await push_branch(wt_path, "cog/6-push")
    # Confirm remote has the branch
    result = subprocess.run(
        ["git", "-C", str(temp_git_repo), "ls-remote", "--heads", "origin", "cog/6-push"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "cog/6-push" in result.stdout
    await remove_worktree(temp_git_repo, wt_path)


async def test_push_with_retry_raises_after_exhausting_attempts(
    temp_git_repo: Path,
) -> None:
    with pytest.raises(GitError):
        await push_with_retry(temp_git_repo, "nonexistent-branch", attempts=1, backoff_seconds=0)


# ---------------------------------------------------------------------------
# scan_orphans
# ---------------------------------------------------------------------------


async def test_scan_orphans_empty_when_no_worktrees_dir(temp_git_repo: Path) -> None:
    result = await scan_orphans(temp_git_repo)
    assert result.cleaned == []
    assert result.pushed == []
    assert result.dirty == []
    assert result.unregistered == []


async def test_scan_orphans_cleans_registered_clean_worktree(temp_git_repo: Path) -> None:
    default = subprocess.run(
        ["git", "-C", str(temp_git_repo), "symbolic-ref", "--short", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    wt_dir = temp_git_repo / ".cog" / "worktrees"
    wt_dir.mkdir(parents=True)
    wt_path = wt_dir / "42-test-item"
    await create_worktree(
        temp_git_repo,
        wt_path,
        "cog/42-test-item",
        start_point=f"origin/{default}",
        create_branch=True,
    )
    result = await scan_orphans(temp_git_repo)
    assert wt_path in result.cleaned
    assert not wt_path.exists()


async def test_scan_orphans_marks_dirty_worktree_as_stuck(temp_git_repo: Path) -> None:
    default = subprocess.run(
        ["git", "-C", str(temp_git_repo), "symbolic-ref", "--short", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    wt_dir = temp_git_repo / ".cog" / "worktrees"
    wt_dir.mkdir(parents=True)
    wt_path = wt_dir / "43-dirty"
    await create_worktree(
        temp_git_repo,
        wt_path,
        "cog/43-dirty",
        start_point=f"origin/{default}",
        create_branch=True,
    )
    # Make it dirty
    (wt_path / "dirty.txt").write_text("untracked")
    result = await scan_orphans(temp_git_repo)
    stuck_paths = [s.path for s in result.dirty]
    assert wt_path in stuck_paths
    assert wt_path.exists()
    # Cleanup
    await discard_worktree(temp_git_repo, wt_path)


async def test_scan_orphans_skips_non_matching_dirs(temp_git_repo: Path) -> None:
    wt_dir = temp_git_repo / ".cog" / "worktrees"
    wt_dir.mkdir(parents=True)
    # Non-matching directory name
    (wt_dir / "not-an-item").mkdir()
    result = await scan_orphans(temp_git_repo)
    # Not matched → no entry anywhere
    unregistered_names = [p.name for p in result.unregistered]
    assert "not-an-item" not in unregistered_names


async def test_scan_orphans_unregistered_pattern_matching_dir(temp_git_repo: Path) -> None:
    wt_dir = temp_git_repo / ".cog" / "worktrees"
    wt_dir.mkdir(parents=True)
    orphan_path = wt_dir / "99-orphaned"
    orphan_path.mkdir()
    # Not registered with git worktree
    result = await scan_orphans(temp_git_repo)
    assert orphan_path in result.unregistered
    orphan_path.rmdir()


# ---------------------------------------------------------------------------
# discard_worktree
# ---------------------------------------------------------------------------


async def test_discard_worktree_removes_registered(temp_git_repo: Path) -> None:
    default = subprocess.run(
        ["git", "-C", str(temp_git_repo), "symbolic-ref", "--short", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    wt_path = temp_git_repo.parent / "wt-discard"
    await create_worktree(
        temp_git_repo,
        wt_path,
        "cog/7-discard",
        start_point=f"origin/{default}",
        create_branch=True,
    )
    await discard_worktree(temp_git_repo, wt_path)
    assert not wt_path.exists()


async def test_discard_worktree_removes_unregistered(temp_git_repo: Path) -> None:
    orphan = temp_git_repo.parent / "orphan-dir"
    orphan.mkdir()
    await discard_worktree(temp_git_repo, orphan)
    assert not orphan.exists()
