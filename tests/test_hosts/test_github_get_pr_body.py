from pathlib import Path

import pytest

from cog.hosts.github import GitHubGitHost
from tests.fakes import FakeSubprocessRegistry

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "gh"


@pytest.fixture
def project_dir(tmp_path):
    return tmp_path


async def test_get_pr_body_happy(project_dir):
    reg = FakeSubprocessRegistry()
    reg.push(stdout=(FIXTURES_DIR / "pr_view_body.json").read_bytes())
    host = GitHubGitHost(project_dir)
    with reg.patch("cog.hosts.github"):
        body = await host.get_pr_body(42)
    assert body == "This PR closes #1 with some changes."
    call = reg.calls[0]
    assert call.argv == ("gh", "pr", "view", "42", "--json", "body")


async def test_get_pr_body_null_body_returns_empty(project_dir):
    reg = FakeSubprocessRegistry()
    reg.push(stdout=b'{"body": null}')
    host = GitHubGitHost(project_dir)
    with reg.patch("cog.hosts.github"):
        body = await host.get_pr_body(42)
    assert body == ""
