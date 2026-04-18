from pathlib import Path

import pytest

from cog.core.errors import HostError
from cog.hosts.github import GitHubGitHost
from tests.fakes import FakeSubprocessRegistry

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "gh"


@pytest.fixture
def project_dir(tmp_path):
    return tmp_path


async def test_gh_nonzero_raises_host_error(project_dir):
    reg = FakeSubprocessRegistry()
    reg.push(returncode=1)
    host = GitHubGitHost(project_dir)
    with reg.patch("cog.hosts.github"):
        with pytest.raises(HostError):
            await host.get_pr_for_branch("my-branch")


async def test_git_nonzero_raises_host_error(project_dir):
    reg = FakeSubprocessRegistry()
    reg.push(returncode=128)
    host = GitHubGitHost(project_dir)
    with reg.patch("cog.hosts.github"):
        with pytest.raises(HostError):
            await host.push_branch("my-branch")


async def test_malformed_json_raises_host_error(project_dir):
    reg = FakeSubprocessRegistry()
    reg.push(stdout=b"not valid json {")
    host = GitHubGitHost(project_dir)
    with reg.patch("cog.hosts.github"):
        with pytest.raises(HostError, match="invalid JSON"):
            await host.get_pr_for_branch("my-branch")
