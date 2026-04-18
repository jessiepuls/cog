from pathlib import Path

import pytest

from cog.core.errors import HostError
from cog.hosts.github import GitHubGitHost
from tests.fakes import FakeSubprocessRegistry

LIST_ARGV = (
    "gh",
    "pr",
    "list",
    "--head",
    "my-branch",
    "--state",
    "open",
    "--json",
    "number,url,state,body,headRefName",
)


async def test_gh_nonzero_raises_host_error(
    registry: FakeSubprocessRegistry,
    project_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry.expect(LIST_ARGV, returncode=1)
    monkeypatch.setattr("asyncio.create_subprocess_exec", registry.create_subprocess_exec)
    host = GitHubGitHost(project_dir)
    with pytest.raises(HostError):
        await host.get_pr_for_branch("my-branch")


async def test_git_nonzero_raises_host_error(
    registry: FakeSubprocessRegistry,
    project_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry.expect(("git", "push", "-u", "origin", "my-branch"), returncode=128)
    monkeypatch.setattr("asyncio.create_subprocess_exec", registry.create_subprocess_exec)
    host = GitHubGitHost(project_dir)
    with pytest.raises(HostError):
        await host.push_branch("my-branch")


async def test_malformed_json_raises_host_error(
    registry: FakeSubprocessRegistry,
    project_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry.expect(LIST_ARGV, stdout=b"not valid json {")
    monkeypatch.setattr("asyncio.create_subprocess_exec", registry.create_subprocess_exec)
    host = GitHubGitHost(project_dir)
    with pytest.raises(HostError, match="invalid JSON"):
        await host.get_pr_for_branch("my-branch")
