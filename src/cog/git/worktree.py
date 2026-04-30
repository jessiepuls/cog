"""Pure functions over the `git worktree` CLI.

All functions shell to git via the async helpers in `cog.git`; no direct
subprocess calls inline. Errors raise typed exceptions from `cog.core.errors`.
"""

from __future__ import annotations

import asyncio
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from subprocess import PIPE

from cog.core.errors import GitError

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WorktreeInfo:
    path: Path
    branch: str | None  # None if detached HEAD
    head: str
    is_locked: bool
    is_prunable: bool


@dataclass(frozen=True)
class StuckWorktree:
    path: Path
    branch: str | None
    item_id: int | None  # parsed from dir name "<id>-<slug>"
    reason: str  # "dirty", "push failed: ...", "not registered with git"


@dataclass
class OrphanScanResult:
    cleaned: list[Path]
    pushed: list[tuple[Path, str]]  # (path, branch)
    dirty: list[StuckWorktree]
    unregistered: list[Path]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_WORKTREE_DIR_RE = re.compile(r"^\d+-[a-z0-9-]+$")
_ITEM_ID_RE = re.compile(r"^(\d+)-")


async def _run(args: list[str], cwd: Path) -> str:
    proc = await asyncio.create_subprocess_exec(*args, cwd=cwd, stdout=PIPE, stderr=PIPE)
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise GitError(
            f"{' '.join(args)} failed (exit {proc.returncode}): {stderr.decode().strip()}"
        )
    return stdout.decode().strip()


def _parse_item_id(dirname: str) -> int | None:
    m = _ITEM_ID_RE.match(dirname)
    return int(m.group(1)) if m else None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def create_worktree(
    repo: Path,
    path: Path,
    branch: str,
    *,
    start_point: str,
    create_branch: bool,
) -> None:
    """`git worktree add [-b <branch>] <path> <start_point>`."""
    if create_branch:
        await _run(["git", "worktree", "add", "-b", branch, str(path), start_point], repo)
    else:
        await _run(["git", "worktree", "add", str(path), branch], repo)


async def remove_worktree(repo: Path, path: Path, *, force: bool = False) -> None:
    """`git worktree remove [--force] <path>`."""
    args = ["git", "worktree", "remove"]
    if force:
        args.append("--force")
    args.append(str(path))
    await _run(args, repo)


async def list_worktrees(repo: Path) -> list[WorktreeInfo]:
    """`git worktree list --porcelain` → list of WorktreeInfo."""
    out = await _run(["git", "worktree", "list", "--porcelain"], repo)
    entries: list[WorktreeInfo] = []
    current: dict[str, str] = {}
    for line in out.splitlines():
        if not line:
            if current:
                entries.append(_parse_worktree_entry(current))
                current = {}
        else:
            if " " in line:
                key, _, val = line.partition(" ")
                current[key] = val
            else:
                current[line] = ""
    if current:
        entries.append(_parse_worktree_entry(current))
    return entries


def _parse_worktree_entry(entry: dict[str, str]) -> WorktreeInfo:
    raw_branch = entry.get("branch", "")
    branch = raw_branch.removeprefix("refs/heads/") if raw_branch else None
    return WorktreeInfo(
        path=Path(entry.get("worktree", "")),
        branch=branch or None,
        head=entry.get("HEAD", ""),
        is_locked="locked" in entry,
        is_prunable="prunable" in entry,
    )


async def prune(repo: Path) -> None:
    """`git worktree prune`."""
    await _run(["git", "worktree", "prune"], repo)


async def is_dirty(path: Path) -> bool:
    """True iff `git -C <path> status --porcelain` produces any output.

    Staged, unstaged tracked changes, and non-ignored untracked files all
    count. Gitignored files do not.
    """
    out = await _run(
        ["git", "-C", str(path), "status", "--porcelain", "--untracked-files=normal"],
        path,
    )
    return bool(out.strip())


async def remote_branch_exists(repo: Path, branch: str) -> bool:
    """True if `origin/<branch>` exists."""
    try:
        await _run(
            ["git", "rev-parse", "--verify", f"refs/remotes/origin/{branch}"],
            repo,
        )
        return True
    except GitError:
        return False


async def is_ahead_of_origin(path: Path, branch: str) -> bool:
    """True if `branch` has commits not on `origin/<branch>` (or origin lacks it)."""
    repo = _find_repo_root(path)
    try:
        count_str = await _run(
            ["git", "rev-list", "--count", f"origin/{branch}..{branch}"],
            repo,
        )
        return int(count_str) > 0
    except GitError:
        # origin/branch doesn't exist — we're ahead by definition
        return True


def _find_repo_root(path: Path) -> Path:
    """Walk up from path to find the repo root (where .git lives)."""
    current = path if path.is_dir() else path.parent
    while True:
        git_path = current / ".git"
        if git_path.exists():
            return current
        parent = current.parent
        if parent == current:
            return path  # fallback: use path itself
        current = parent


async def push_branch(path: Path, branch: str) -> None:
    """`git -C <path> push origin <branch>`."""
    await _run(["git", "-C", str(path), "push", "origin", branch], path)


async def push_with_retry(
    path: Path,
    branch: str,
    *,
    attempts: int = 2,
    backoff_seconds: float = 5.0,
) -> None:
    """Push branch, retrying on failure with a short backoff."""
    last_exc: GitError | None = None
    for i in range(attempts):
        try:
            await push_branch(path, branch)
            return
        except GitError as e:
            last_exc = e
            if i < attempts - 1:
                await asyncio.sleep(backoff_seconds)
    raise last_exc  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Orphan scan
# ---------------------------------------------------------------------------


async def scan_orphans(project_dir: Path) -> OrphanScanResult:
    """Scan .cog/worktrees/ for orphaned worktrees and recover what we can.

    Steps:
    1. git worktree prune
    2. List entries under <project_dir>/.cog/worktrees/
    3. Filter to those matching <id>-<slug> pattern
    4. Classify each against `git worktree list --porcelain` and act

    Returns an OrphanScanResult describing what happened.
    """
    result = OrphanScanResult(cleaned=[], pushed=[], dirty=[], unregistered=[])
    worktrees_dir = project_dir / ".cog" / "worktrees"
    if not worktrees_dir.is_dir():
        return result

    await prune(project_dir)

    try:
        registered = await list_worktrees(project_dir)
    except GitError:
        return result

    registered_paths = {wt.path for wt in registered}
    registered_by_path = {wt.path: wt for wt in registered}

    for entry in worktrees_dir.iterdir():
        if not entry.is_dir():
            continue
        if not _WORKTREE_DIR_RE.match(entry.name):
            continue

        item_id = _parse_item_id(entry.name)

        if entry not in registered_paths:
            result.unregistered.append(entry)
            continue

        wt = registered_by_path[entry]
        branch = wt.branch

        dirty = await is_dirty(entry)
        if dirty:
            result.dirty.append(
                StuckWorktree(
                    path=entry,
                    branch=branch,
                    item_id=item_id,
                    reason="dirty",
                )
            )
            continue

        if branch and await is_ahead_of_origin(entry, branch):
            try:
                await push_with_retry(entry, branch)
                result.pushed.append((entry, branch))
            except GitError as e:
                result.dirty.append(
                    StuckWorktree(
                        path=entry,
                        branch=branch,
                        item_id=item_id,
                        reason=f"push failed: {e}",
                    )
                )
                continue

        try:
            await remove_worktree(project_dir, entry)
            result.cleaned.append(entry)
        except GitError as e:
            result.dirty.append(
                StuckWorktree(
                    path=entry,
                    branch=branch,
                    item_id=item_id,
                    reason=f"remove failed: {e}",
                )
            )

    return result


async def discard_worktree(project_dir: Path, path: Path) -> None:
    """Force-remove a worktree. Falls back to shutil.rmtree if git fails."""
    try:
        await remove_worktree(project_dir, path, force=True)
    except GitError:
        if path.exists():
            shutil.rmtree(path, ignore_errors=True)
