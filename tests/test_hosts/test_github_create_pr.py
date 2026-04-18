import pytest

from cog.core.errors import HostError
from cog.core.host import PullRequest
from cog.hosts.github import GitHubGitHost
from tests.fakes import FakeSubprocessRegistry

PR_URL = "https://github.com/jessiepuls/cog/pull/42"


@pytest.fixture
def project_dir(tmp_path):
    return tmp_path


async def test_create_pr_argv_and_stdin(project_dir):
    reg = FakeSubprocessRegistry()
    reg.push(stdout=f"{PR_URL}\n".encode())
    host = GitHubGitHost(project_dir)
    with reg.patch("cog.hosts.github"):
        await host.create_pr(head="my-branch", title="My PR", body="PR body")
    call = reg.calls[0]
    assert call.argv == (
        "gh",
        "pr",
        "create",
        "--head",
        "my-branch",
        "--title",
        "My PR",
        "--body-file",
        "-",
    )
    assert call.stdin == b"PR body"


async def test_create_pr_parses_url_and_number(project_dir):
    reg = FakeSubprocessRegistry()
    reg.push(stdout=f"{PR_URL}\n".encode())
    host = GitHubGitHost(project_dir)
    with reg.patch("cog.hosts.github"):
        pr = await host.create_pr(head="my-branch", title="My PR", body="PR body")
    assert pr == PullRequest(
        number=42, url=PR_URL, state="open", body="PR body", head_branch="my-branch"
    )


async def test_create_pr_multiline_stdout_uses_last_line(project_dir):
    reg = FakeSubprocessRegistry()
    reg.push(stdout=f"Creating pull request...\nSome progress...\n{PR_URL}\n".encode())
    host = GitHubGitHost(project_dir)
    with reg.patch("cog.hosts.github"):
        pr = await host.create_pr(head="my-branch", title="My PR", body="PR body")
    assert pr.url == PR_URL
    assert pr.number == 42


async def test_create_pr_unexpected_stdout_raises(project_dir):
    reg = FakeSubprocessRegistry()
    reg.push(stdout=b"not a url\n")
    host = GitHubGitHost(project_dir)
    with reg.patch("cog.hosts.github"):
        with pytest.raises(HostError, match="unrecognized URL"):
            await host.create_pr(head="my-branch", title="My PR", body="PR body")


async def test_create_pr_empty_stdout_raises(project_dir):
    reg = FakeSubprocessRegistry()
    reg.push(stdout=b"")
    host = GitHubGitHost(project_dir)
    with reg.patch("cog.hosts.github"):
        with pytest.raises(HostError, match="empty stdout"):
            await host.create_pr(head="my-branch", title="My PR", body="PR body")
