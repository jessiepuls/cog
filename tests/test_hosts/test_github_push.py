import pytest

from cog.core.errors import HostError
from cog.hosts.github import GitHubGitHost
from tests.fakes import FakeSubprocessRegistry


@pytest.fixture
def project_dir(tmp_path):
    return tmp_path


async def test_push_branch_argv(project_dir):
    reg = FakeSubprocessRegistry()
    reg.push()
    host = GitHubGitHost(project_dir)
    with reg.patch("cog.hosts.github"):
        await host.push_branch("my-branch")
    call = reg.calls[0]
    assert call.argv == ("git", "push", "-u", "origin", "my-branch")
    assert call.cwd == project_dir


async def test_push_branch_nonzero_raises_host_error(project_dir):
    reg = FakeSubprocessRegistry()
    reg.push(returncode=1)
    host = GitHubGitHost(project_dir)
    with reg.patch("cog.hosts.github"):
        with pytest.raises(HostError):
            await host.push_branch("my-branch")
