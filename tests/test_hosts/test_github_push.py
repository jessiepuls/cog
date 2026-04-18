from pathlib import Path

import pytest

from cog.core.errors import HostError
from cog.hosts.github import GitHubGitHost
from tests.fakes import FakeSubprocessRegistry


async def test_push_branch_argv(
    registry: FakeSubprocessRegistry,
    project_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry.expect(("git", "push", "-u", "origin", "my-branch"))
    monkeypatch.setattr("asyncio.create_subprocess_exec", registry.create_subprocess_exec)
    host = GitHubGitHost(project_dir)
    await host.push_branch("my-branch")
    assert ("git", "push", "-u", "origin", "my-branch") in registry.calls


async def test_push_branch_nonzero_raises_host_error(
    registry: FakeSubprocessRegistry,
    project_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry.expect(("git", "push", "-u", "origin", "my-branch"), returncode=1)
    monkeypatch.setattr("asyncio.create_subprocess_exec", registry.create_subprocess_exec)
    host = GitHubGitHost(project_dir)
    with pytest.raises(HostError):
        await host.push_branch("my-branch")
