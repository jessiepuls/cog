from pathlib import Path

import pytest

from cog.hosts.github import GitHubGitHost
from tests.fakes import FakeSubprocessRegistry

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "gh"


@pytest.fixture
def project_dir(tmp_path):
    return tmp_path


async def test_single_result(project_dir):
    reg = FakeSubprocessRegistry()
    reg.push(stdout=(FIXTURES_DIR / "pr_list_single.json").read_bytes())
    host = GitHubGitHost(project_dir)
    with reg.patch("cog.hosts.github"):
        pr = await host.get_pr_for_branch("feature/my-branch")
    assert pr is not None
    assert pr.number == 42
    assert pr.url == "https://github.com/jessiepuls/cog/pull/42"
    assert pr.head_branch == "feature/my-branch"


async def test_empty_result(project_dir):
    reg = FakeSubprocessRegistry()
    reg.push(stdout=(FIXTURES_DIR / "pr_list_empty.json").read_bytes())
    host = GitHubGitHost(project_dir)
    with reg.patch("cog.hosts.github"):
        pr = await host.get_pr_for_branch("feature/my-branch")
    assert pr is None


async def test_multiple_returns_first(project_dir):
    reg = FakeSubprocessRegistry()
    reg.push(stdout=(FIXTURES_DIR / "pr_list_multiple_head.json").read_bytes())
    host = GitHubGitHost(project_dir)
    with reg.patch("cog.hosts.github"):
        pr = await host.get_pr_for_branch("feature/my-branch")
    assert pr is not None
    assert pr.number == 43  # first (newest) in fixture


async def test_state_filter_open(project_dir):
    reg = FakeSubprocessRegistry()
    reg.push(stdout=(FIXTURES_DIR / "pr_list_empty.json").read_bytes())
    host = GitHubGitHost(project_dir)
    with reg.patch("cog.hosts.github"):
        await host.get_pr_for_branch("feature/my-branch")
    argv = reg.calls[0].argv
    state_idx = argv.index("--state")
    assert argv[state_idx + 1] == "open"


async def test_json_field_list(project_dir):
    reg = FakeSubprocessRegistry()
    reg.push(stdout=(FIXTURES_DIR / "pr_list_empty.json").read_bytes())
    host = GitHubGitHost(project_dir)
    with reg.patch("cog.hosts.github"):
        await host.get_pr_for_branch("feature/my-branch")
    argv = reg.calls[0].argv
    json_idx = argv.index("--json")
    assert argv[json_idx + 1] == "number,url,state,body,headRefName"


async def test_state_lowercased(project_dir):
    reg = FakeSubprocessRegistry()
    reg.push(stdout=(FIXTURES_DIR / "pr_list_single.json").read_bytes())
    host = GitHubGitHost(project_dir)
    with reg.patch("cog.hosts.github"):
        pr = await host.get_pr_for_branch("feature/my-branch")
    assert pr is not None
    assert pr.state == "open"
