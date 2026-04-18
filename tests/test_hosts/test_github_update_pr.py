from pathlib import Path

import pytest

from cog.hosts.github import GitHubGitHost
from tests.fakes import FakeSubprocessRegistry

UPDATE_ARGV = ("gh", "pr", "edit", "42", "--body-file", "-")


async def test_update_pr_argv_and_stdin(
    registry: FakeSubprocessRegistry,
    project_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    proc = registry.expect(UPDATE_ARGV)
    monkeypatch.setattr("asyncio.create_subprocess_exec", registry.create_subprocess_exec)
    host = GitHubGitHost(project_dir)
    await host.update_pr(42, body="new body")
    assert UPDATE_ARGV in registry.calls
    assert proc.received_stdin == b"new body"
