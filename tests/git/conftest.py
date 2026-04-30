"""Fixtures for git/worktree tests that use real git repos."""

import subprocess
from pathlib import Path

import pytest


@pytest.fixture
def temp_git_repo(tmp_path: Path) -> Path:
    """Real git repo with one commit and an `origin` bare remote.

    Layout:
        tmp_path/origin.git  — bare remote
        tmp_path/repo        — working clone
    Returns the path to the working clone.
    """
    origin = tmp_path / "origin.git"
    repo = tmp_path / "repo"

    # Init bare remote
    subprocess.run(["git", "init", "--bare", str(origin)], check=True, capture_output=True)

    # Init working repo
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.email", "test@example.com"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.name", "Test User"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "remote", "add", "origin", str(origin)],
        check=True,
        capture_output=True,
    )

    # Seed commit
    seed = repo / "README.md"
    seed.write_text("seed")
    subprocess.run(["git", "-C", str(repo), "add", "README.md"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", "Initial commit"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "push", "-u", "origin", "HEAD"],
        check=True,
        capture_output=True,
    )

    # Set up origin/HEAD
    default = subprocess.run(
        ["git", "-C", str(repo), "symbolic-ref", "--short", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    subprocess.run(
        ["git", "-C", str(origin), "symbolic-ref", "HEAD", f"refs/heads/{default}"],
        check=True,
        capture_output=True,
    )
    # Make origin/HEAD resolvable
    subprocess.run(
        ["git", "-C", str(repo), "remote", "set-head", "origin", default],
        check=True,
        capture_output=True,
    )

    return repo
