from pathlib import Path

import pytest

from cog.hosts.github import GitHubGitHost
from tests.fakes import FakeSubprocessRegistry
from tests.test_hosts.conftest import load_fixture

GET_BODY_ARGV = ("gh", "pr", "view", "42", "--json", "body")


async def test_get_pr_body_happy(
    registry: FakeSubprocessRegistry,
    project_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry.expect(GET_BODY_ARGV, stdout=load_fixture("pr_view_body.json"))
    monkeypatch.setattr("asyncio.create_subprocess_exec", registry.create_subprocess_exec)
    host = GitHubGitHost(project_dir)
    body = await host.get_pr_body(42)
    assert body == "This PR closes #1 with some changes."
    assert GET_BODY_ARGV in registry.calls


async def test_get_pr_body_null_body_returns_empty(
    registry: FakeSubprocessRegistry,
    project_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry.expect(GET_BODY_ARGV, stdout=b'{"body": null}')
    monkeypatch.setattr("asyncio.create_subprocess_exec", registry.create_subprocess_exec)
    host = GitHubGitHost(project_dir)
    body = await host.get_pr_body(42)
    assert body == ""
