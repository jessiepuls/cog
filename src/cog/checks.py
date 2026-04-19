"""Concrete preflight check implementations and named bundles."""

import asyncio
import os
import shutil
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any, Literal

from cog.core.preflight import PreflightCheck, PreflightResult

_SubprocessFactory = Callable[..., Coroutine[Any, Any, Any]]
_WhichFn = Callable[[str], str | None]


async def _run_cmd(
    factory: _SubprocessFactory | None,
    *args: str,
    cwd: Path | None = None,
) -> tuple[int, bytes, bytes]:
    """Run a subprocess, wait for completion, return (returncode, stdout, stderr)."""
    create = factory or asyncio.create_subprocess_exec
    kwargs: dict[str, Any] = {
        "stdout": asyncio.subprocess.PIPE,
        "stderr": asyncio.subprocess.PIPE,
    }
    if cwd is not None:
        kwargs["cwd"] = cwd
    proc = await create(*args, **kwargs)
    out, err = await proc.communicate()
    rc: int = proc.returncode  # type: ignore[assignment]  # guaranteed set after communicate()
    return rc, bytes(out), bytes(err)


class CheckHostTool:
    """Verifies a host binary is on PATH. Parametric: CheckHostTool('git'), etc."""

    level: Literal["error", "warning"] = "error"

    def __init__(self, binary: str, _which: _WhichFn | None = None) -> None:
        self._binary = binary
        self.name = f"host_tool.{binary}"
        self._which = _which

    async def run(self, project_dir: Path) -> PreflightResult:
        which = self._which or shutil.which
        found = await asyncio.to_thread(which, self._binary)
        if found:
            return PreflightResult(
                check=self.name, ok=True, level="error", message=f"{self._binary} binary found"
            )
        return PreflightResult(
            check=self.name, ok=False, level="error", message=f"{self._binary} is not installed"
        )


class CheckGitRepo:
    name: str = "git_repo"
    level: Literal["error", "warning"] = "error"

    def __init__(self, _create_subprocess: _SubprocessFactory | None = None) -> None:
        self._create_subprocess = _create_subprocess

    async def run(self, project_dir: Path) -> PreflightResult:
        rc, _, _ = await _run_cmd(
            self._create_subprocess, "git", "rev-parse", "--is-inside-work-tree", cwd=project_dir
        )
        if rc == 0:
            return PreflightResult(
                check=self.name, ok=True, level="error", message="inside a git repository"
            )
        return PreflightResult(
            check=self.name, ok=False, level="error", message="not inside a git repository"
        )


class CheckCleanTree:
    name: str = "clean_tree"
    level: Literal["error", "warning"] = "error"

    def __init__(self, _create_subprocess: _SubprocessFactory | None = None) -> None:
        self._create_subprocess = _create_subprocess

    async def run(self, project_dir: Path) -> PreflightResult:
        issues = []

        rc, _, _ = await _run_cmd(
            self._create_subprocess, "git", "diff", "--quiet", cwd=project_dir
        )
        if rc != 0:
            issues.append("unstaged changes")

        rc, _, _ = await _run_cmd(
            self._create_subprocess, "git", "diff", "--cached", "--quiet", cwd=project_dir
        )
        if rc != 0:
            issues.append("staged changes")

        rc, status_out, _ = await _run_cmd(
            self._create_subprocess,
            "git",
            "status",
            "--porcelain",
            "--untracked-files=normal",
            cwd=project_dir,
        )
        untracked = [ln for ln in status_out.decode().splitlines() if ln.startswith("??")]
        if untracked:
            n = len(untracked)
            issues.append(f"{n} untracked file{'s' if n != 1 else ''}")

        if not issues:
            return PreflightResult(
                check=self.name, ok=True, level="error", message="working tree is clean"
            )
        return PreflightResult(
            check=self.name,
            ok=False,
            level="error",
            message=f"{', '.join(issues)}; run: git stash or commit first",
        )


class CheckDefaultBranch:
    name: str = "default_branch"
    level: Literal["error", "warning"] = "error"

    def __init__(self, _create_subprocess: _SubprocessFactory | None = None) -> None:
        self._create_subprocess = _create_subprocess

    async def run(self, project_dir: Path) -> PreflightResult:
        rc, origin_out, _ = await _run_cmd(
            self._create_subprocess,
            "git",
            "symbolic-ref",
            "--short",
            "refs/remotes/origin/HEAD",
            cwd=project_dir,
        )
        if rc != 0:
            return PreflightResult(
                check=self.name,
                ok=False,
                level="error",
                message="run: git remote set-head origin --auto",
            )

        default_branch = origin_out.decode().strip().removeprefix("origin/")

        rc, head_out, _ = await _run_cmd(
            self._create_subprocess,
            "git",
            "symbolic-ref",
            "--short",
            "HEAD",
            cwd=project_dir,
        )
        if rc != 0:
            return PreflightResult(
                check=self.name,
                ok=False,
                level="error",
                message="not on any branch (detached HEAD)",
            )

        current = head_out.decode().strip()
        if current != default_branch:
            return PreflightResult(
                check=self.name,
                ok=False,
                level="error",
                message=f"currently on '{current}'; must be on '{default_branch}'",
            )
        return PreflightResult(
            check=self.name,
            ok=True,
            level="error",
            message=f"on default branch '{default_branch}'",
        )


class CheckGhAuth:
    name: str = "gh_auth"
    level: Literal["error", "warning"] = "error"

    def __init__(self, _create_subprocess: _SubprocessFactory | None = None) -> None:
        self._create_subprocess = _create_subprocess

    async def run(self, project_dir: Path) -> PreflightResult:
        rc, _, _ = await _run_cmd(self._create_subprocess, "gh", "auth", "status")
        if rc == 0:
            return PreflightResult(
                check=self.name, ok=True, level="error", message="gh auth status ok"
            )
        return PreflightResult(
            check=self.name, ok=False, level="error", message="run: gh auth login"
        )


class CheckGhTokenFile:
    name: str = "gh_token_file"
    level: Literal["error", "warning"] = "error"

    def __init__(self, _home_dir: Path | None = None) -> None:
        self._home_dir = _home_dir

    async def run(self, project_dir: Path) -> PreflightResult:
        home = self._home_dir or Path.home()
        hosts_file = home / ".config" / "gh" / "hosts.yml"
        if not hosts_file.exists() or "oauth_token:" not in hosts_file.read_text():
            return PreflightResult(
                check=self.name,
                ok=False,
                level="error",
                message=(
                    "gh token must be file-based, not macOS keychain"
                    " (run: gh auth login --insecure-storage)"
                ),
            )
        return PreflightResult(
            check=self.name, ok=True, level="error", message="gh token file found"
        )


class CheckOriginRemote:
    name: str = "origin_remote"
    level: Literal["error", "warning"] = "error"

    def __init__(self, _create_subprocess: _SubprocessFactory | None = None) -> None:
        self._create_subprocess = _create_subprocess

    async def run(self, project_dir: Path) -> PreflightResult:
        rc, _, _ = await _run_cmd(
            self._create_subprocess, "git", "remote", "get-url", "origin", cwd=project_dir
        )
        if rc == 0:
            return PreflightResult(
                check=self.name, ok=True, level="error", message="origin remote configured"
            )
        return PreflightResult(
            check=self.name, ok=False, level="error", message="no 'origin' remote configured"
        )


class CheckDockerRunning:
    name: str = "docker_running"
    level: Literal["error", "warning"] = "error"

    def __init__(self, _create_subprocess: _SubprocessFactory | None = None) -> None:
        self._create_subprocess = _create_subprocess

    async def run(self, project_dir: Path) -> PreflightResult:
        rc, _, _ = await _run_cmd(self._create_subprocess, "docker", "info")
        if rc == 0:
            return PreflightResult(
                check=self.name, ok=True, level="error", message="docker daemon running"
            )
        return PreflightResult(
            check=self.name,
            ok=False,
            level="error",
            message="docker daemon not running (start Docker Desktop or systemctl start docker)",
        )


class CheckClaudeAuth:
    name: str = "claude_auth"
    level: Literal["error", "warning"] = "warning"

    def __init__(
        self,
        _env: dict[str, str] | None = None,
        _which: _WhichFn | None = None,
        _create_subprocess: _SubprocessFactory | None = None,
    ) -> None:
        self._env = _env
        self._which = _which
        self._create_subprocess = _create_subprocess

    async def run(self, project_dir: Path) -> PreflightResult:
        env = self._env if self._env is not None else os.environ
        if env.get("ANTHROPIC_API_KEY"):
            return PreflightResult(
                check=self.name, ok=True, level="warning", message="ANTHROPIC_API_KEY is set"
            )

        which = self._which or shutil.which
        security_path = await asyncio.to_thread(which, "security")
        if not security_path:
            return PreflightResult(
                check=self.name,
                ok=False,
                level="warning",
                message=(
                    "neither ANTHROPIC_API_KEY nor macOS keychain entry found;"
                    " claude inside container will fail if auth is absent elsewhere"
                ),
            )

        rc, _, _ = await _run_cmd(
            self._create_subprocess,
            "security",
            "find-generic-password",
            "-s",
            "Claude Code-credentials",
            "-w",
        )
        if rc == 0:
            return PreflightResult(
                check=self.name, ok=True, level="warning", message="macOS keychain entry found"
            )
        return PreflightResult(
            check=self.name,
            ok=False,
            level="warning",
            message=(
                "neither ANTHROPIC_API_KEY nor macOS keychain entry found;"
                " claude inside container will fail if auth is absent elsewhere"
            ),
        )


# Used by cog doctor — the maximal check set
ALL_CHECKS: tuple[PreflightCheck, ...] = (
    CheckHostTool("git"),
    CheckHostTool("gh"),
    CheckHostTool("docker"),
    CheckGitRepo(),
    CheckCleanTree(),
    CheckDefaultBranch(),
    CheckOriginRemote(),
    CheckGhAuth(),
    CheckGhTokenFile(),
    CheckDockerRunning(),
    CheckClaudeAuth(),
)

# Workflow preflight — excludes git-state checks (clean_tree, default_branch).
# Those are redundant at startup: RalphWorkflow.pre_stages does
# `checkout default → fetch → merge-ff → create work branch` and git itself
# will fail cleanly if the tree isn't checkout-able at that moment. Preempting
# before the user has even picked an item is friction without coverage.
# `cog doctor` still surfaces git-state via ALL_CHECKS for diagnostic use.
_WORKFLOW_CHECKS: tuple[PreflightCheck, ...] = tuple(
    c for c in ALL_CHECKS if c.name not in ("clean_tree", "default_branch")
)
RALPH_CHECKS: tuple[PreflightCheck, ...] = _WORKFLOW_CHECKS
REFINE_CHECKS: tuple[PreflightCheck, ...] = _WORKFLOW_CHECKS
