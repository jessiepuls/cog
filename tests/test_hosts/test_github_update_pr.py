import pytest

from cog.hosts.github import GitHubGitHost
from tests.fakes import FakeSubprocessRegistry


@pytest.fixture
def project_dir(tmp_path):
    return tmp_path


async def test_update_pr_argv_and_stdin(project_dir):
    reg = FakeSubprocessRegistry()
    reg.push()
    host = GitHubGitHost(project_dir)
    with reg.patch("cog.hosts.github"):
        await host.update_pr(42, body="new body")
    call = reg.calls[0]
    assert call.argv == ("gh", "pr", "edit", "42", "--body-file", "-")
    assert call.stdin == b"new body"
