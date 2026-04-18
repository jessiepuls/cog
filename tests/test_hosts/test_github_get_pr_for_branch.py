from pathlib import Path

import pytest

from cog.hosts.github import GitHubGitHost
from tests.fakes import FakeSubprocessRegistry
from tests.test_hosts.conftest import load_fixture

LIST_ARGV = (
    "gh",
    "pr",
    "list",
    "--head",
    "feature/my-branch",
    "--state",
    "open",
    "--json",
    "number,url,state,body,headRefName",
)


async def test_single_result(
    registry: FakeSubprocessRegistry,
    project_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry.expect(LIST_ARGV, stdout=load_fixture("pr_list_single.json"))
    monkeypatch.setattr("asyncio.create_subprocess_exec", registry.create_subprocess_exec)
    host = GitHubGitHost(project_dir)
    pr = await host.get_pr_for_branch("feature/my-branch")
    assert pr is not None
    assert pr.number == 42
    assert pr.url == "https://github.com/jessiepuls/cog/pull/42"
    assert pr.head_branch == "feature/my-branch"


async def test_empty_result(
    registry: FakeSubprocessRegistry,
    project_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry.expect(LIST_ARGV, stdout=load_fixture("pr_list_empty.json"))
    monkeypatch.setattr("asyncio.create_subprocess_exec", registry.create_subprocess_exec)
    host = GitHubGitHost(project_dir)
    pr = await host.get_pr_for_branch("feature/my-branch")
    assert pr is None


async def test_multiple_returns_first(
    registry: FakeSubprocessRegistry,
    project_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry.expect(LIST_ARGV, stdout=load_fixture("pr_list_multiple_head.json"))
    monkeypatch.setattr("asyncio.create_subprocess_exec", registry.create_subprocess_exec)
    host = GitHubGitHost(project_dir)
    pr = await host.get_pr_for_branch("feature/my-branch")
    assert pr is not None
    assert pr.number == 43  # first (newest) in fixture


async def test_state_filter_open(
    registry: FakeSubprocessRegistry,
    project_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry.expect(LIST_ARGV, stdout=load_fixture("pr_list_empty.json"))
    monkeypatch.setattr("asyncio.create_subprocess_exec", registry.create_subprocess_exec)
    host = GitHubGitHost(project_dir)
    await host.get_pr_for_branch("feature/my-branch")
    argv = registry.calls[0]
    state_idx = argv.index("--state")
    assert argv[state_idx + 1] == "open"


async def test_json_field_list(
    registry: FakeSubprocessRegistry,
    project_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry.expect(LIST_ARGV, stdout=load_fixture("pr_list_empty.json"))
    monkeypatch.setattr("asyncio.create_subprocess_exec", registry.create_subprocess_exec)
    host = GitHubGitHost(project_dir)
    await host.get_pr_for_branch("feature/my-branch")
    argv = registry.calls[0]
    json_idx = argv.index("--json")
    assert argv[json_idx + 1] == "number,url,state,body,headRefName"


async def test_state_lowercased(
    registry: FakeSubprocessRegistry,
    project_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry.expect(LIST_ARGV, stdout=load_fixture("pr_list_single.json"))
    monkeypatch.setattr("asyncio.create_subprocess_exec", registry.create_subprocess_exec)
    host = GitHubGitHost(project_dir)
    pr = await host.get_pr_for_branch("feature/my-branch")
    assert pr is not None
    assert pr.state == "open"
