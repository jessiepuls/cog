"""Async git subprocess helpers. All failures raise GitError."""

import asyncio
from pathlib import Path
from subprocess import PIPE

from cog.core.errors import GitError


async def _run(args: list[str], cwd: Path) -> str:
    proc = await asyncio.create_subprocess_exec(*args, cwd=cwd, stdout=PIPE, stderr=PIPE)
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise GitError(
            f"{' '.join(args)} failed (exit {proc.returncode}): {stderr.decode().strip()}"
        )
    return stdout.decode().strip()


async def default_branch(project_dir: Path) -> str:
    """`git symbolic-ref --short refs/remotes/origin/HEAD` → 'main' (stripped of 'origin/')."""
    result = await _run(["git", "symbolic-ref", "--short", "refs/remotes/origin/HEAD"], project_dir)
    return result.removeprefix("origin/")


async def current_branch(project_dir: Path) -> str:
    """`git symbolic-ref --short HEAD`. Raises GitError on detached HEAD."""
    return await _run(["git", "symbolic-ref", "--short", "HEAD"], project_dir)


async def current_head_sha(project_dir: Path) -> str:
    """`git rev-parse HEAD` → SHA."""
    return await _run(["git", "rev-parse", "HEAD"], project_dir)


async def commits_between(project_dir: Path, from_sha: str, to_sha: str = "HEAD") -> int:
    """`git rev-list --count <from>..<to>`."""
    result = await _run(["git", "rev-list", "--count", f"{from_sha}..{to_sha}"], project_dir)
    return int(result)


async def checkout_branch(project_dir: Path, branch: str) -> None:
    """`git checkout <branch>`."""
    await _run(["git", "checkout", branch], project_dir)


async def fetch_origin(project_dir: Path) -> None:
    """`git fetch origin`."""
    await _run(["git", "fetch", "origin"], project_dir)


async def merge_ff_only(project_dir: Path, ref: str) -> None:
    """`git merge --ff-only <ref>`. Raises GitError on non-ff state."""
    await _run(["git", "merge", "--ff-only", ref], project_dir)


async def create_branch(project_dir: Path, name: str, start_point: str = "HEAD") -> None:
    """`git checkout -b <name> <start_point>`. Raises GitError if branch already exists."""
    await _run(["git", "checkout", "-b", name, start_point], project_dir)


async def branch_exists(project_dir: Path, name: str) -> bool:
    """`git rev-parse --verify refs/heads/<name>` → True if branch exists locally."""
    try:
        await _run(["git", "rev-parse", "--verify", f"refs/heads/{name}"], project_dir)
        return True
    except GitError:
        return False


async def delete_branch(project_dir: Path, name: str) -> None:
    """`git branch -D <name>` — force delete (branch may not be merged)."""
    await _run(["git", "branch", "-D", name], project_dir)


async def log_short_shas(project_dir: Path, revision_range: str) -> list[str]:
    """`git log --format=%h --no-merges <range>` → list of short SHAs (oldest-first empty ok)."""
    result = await _run(
        ["git", "log", "--format=%h", "--no-merges", revision_range],
        project_dir,
    )
    return [s for s in result.splitlines() if s]


async def rebase_in_progress(project_dir: Path) -> bool:
    """True if a git rebase is currently paused (conflict or interactive).

    Checks for .git/rebase-merge/ or .git/rebase-apply/ — git's canonical
    mid-rebase markers.
    """
    git_dir = project_dir / ".git"
    return (git_dir / "rebase-merge").is_dir() or (git_dir / "rebase-apply").is_dir()


async def rebase_abort(project_dir: Path) -> None:
    """`git rebase --abort`. No-op if no rebase in progress."""
    await _run(["git", "rebase", "--abort"], project_dir)
