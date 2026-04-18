from pathlib import Path

import pytest

from cog.core.errors import HostError
from cog.core.host import PullRequest
from cog.hosts.github import GitHubGitHost
from tests.fakes import FakeSubprocessRegistry

PR_URL = "https://github.com/jessiepuls/cog/pull/42"
CREATE_ARGV = (
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


async def test_create_pr_argv_and_stdin(
    registry: FakeSubprocessRegistry,
    project_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    proc = registry.expect(CREATE_ARGV, stdout=f"{PR_URL}\n".encode())
    monkeypatch.setattr("asyncio.create_subprocess_exec", registry.create_subprocess_exec)
    host = GitHubGitHost(project_dir)
    await host.create_pr(head="my-branch", title="My PR", body="PR body")
    assert CREATE_ARGV in registry.calls
    assert proc.received_stdin == b"PR body"


async def test_create_pr_parses_url_and_number(
    registry: FakeSubprocessRegistry,
    project_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry.expect(CREATE_ARGV, stdout=f"{PR_URL}\n".encode())
    monkeypatch.setattr("asyncio.create_subprocess_exec", registry.create_subprocess_exec)
    host = GitHubGitHost(project_dir)
    pr = await host.create_pr(head="my-branch", title="My PR", body="PR body")
    assert pr == PullRequest(
        number=42, url=PR_URL, state="open", body="PR body", head_branch="my-branch"
    )


async def test_create_pr_multiline_stdout_uses_last_line(
    registry: FakeSubprocessRegistry,
    project_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry.expect(
        CREATE_ARGV,
        stdout=f"Creating pull request...\nSome progress...\n{PR_URL}\n".encode(),
    )
    monkeypatch.setattr("asyncio.create_subprocess_exec", registry.create_subprocess_exec)
    host = GitHubGitHost(project_dir)
    pr = await host.create_pr(head="my-branch", title="My PR", body="PR body")
    assert pr.url == PR_URL
    assert pr.number == 42


async def test_create_pr_unexpected_stdout_raises(
    registry: FakeSubprocessRegistry,
    project_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry.expect(CREATE_ARGV, stdout=b"not a url\n")
    monkeypatch.setattr("asyncio.create_subprocess_exec", registry.create_subprocess_exec)
    host = GitHubGitHost(project_dir)
    with pytest.raises(HostError, match="unrecognized URL"):
        await host.create_pr(head="my-branch", title="My PR", body="PR body")


async def test_create_pr_empty_stdout_raises(
    registry: FakeSubprocessRegistry,
    project_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry.expect(CREATE_ARGV, stdout=b"")
    monkeypatch.setattr("asyncio.create_subprocess_exec", registry.create_subprocess_exec)
    host = GitHubGitHost(project_dir)
    with pytest.raises(HostError, match="empty stdout"):
        await host.create_pr(head="my-branch", title="My PR", body="PR body")
