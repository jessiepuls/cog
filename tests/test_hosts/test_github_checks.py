"""Tests for GitHubGitHost.get_pr_checks and comment_on_pr."""

import json
from pathlib import Path

import pytest

from cog.core.errors import HostError
from cog.hosts.github import GitHubGitHost
from tests.fakes import FakeSubprocessRegistry

_CHECKS_ARGV = ("gh", "pr", "checks", "42", "--json", "name,state,link,description")


def _checks_json(runs: list[dict]) -> bytes:
    return json.dumps(runs).encode()


@pytest.mark.parametrize(
    "gh_state,expected",
    [
        ("SUCCESS", "passed"),
        ("FAILURE", "failed"),
        ("PENDING", "pending"),
        ("QUEUED", "pending"),
        ("IN_PROGRESS", "pending"),
        ("SKIPPED", "skipped"),
    ],
)
async def test_get_pr_checks_maps_gh_states_to_our_literals(
    gh_state: str,
    expected: str,
    registry: FakeSubprocessRegistry,
    project_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = [{"name": "ci", "state": gh_state, "link": "https://example.com", "description": ""}]
    registry.expect(_CHECKS_ARGV, stdout=_checks_json(payload))
    monkeypatch.setattr("asyncio.create_subprocess_exec", registry.create_subprocess_exec)
    host = GitHubGitHost(project_dir)
    checks = await host.get_pr_checks(42)
    assert len(checks.runs) == 1
    assert checks.runs[0].state == expected


async def test_get_pr_checks_unknown_state_maps_to_pending(
    registry: FakeSubprocessRegistry,
    project_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = [{"name": "ci", "state": "UNKNOWN_STATE", "link": "", "description": ""}]
    registry.expect(_CHECKS_ARGV, stdout=_checks_json(payload))
    monkeypatch.setattr("asyncio.create_subprocess_exec", registry.create_subprocess_exec)
    host = GitHubGitHost(project_dir)
    checks = await host.get_pr_checks(42)
    assert checks.runs[0].state == "pending"


async def test_get_pr_checks_empty_runs_returns_all_passed_true(
    registry: FakeSubprocessRegistry,
    project_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry.expect(_CHECKS_ARGV, stdout=b"[]")
    monkeypatch.setattr("asyncio.create_subprocess_exec", registry.create_subprocess_exec)
    host = GitHubGitHost(project_dir)
    checks = await host.get_pr_checks(42)
    assert checks.all_passed is True
    assert checks.pending is False
    assert checks.failed == ()


async def test_get_pr_checks_all_passed_when_mix_of_passed_and_skipped(
    registry: FakeSubprocessRegistry,
    project_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = [
        {"name": "lint", "state": "SUCCESS", "link": "https://a.com", "description": ""},
        {"name": "docs", "state": "SKIPPED", "link": "https://b.com", "description": ""},
    ]
    registry.expect(_CHECKS_ARGV, stdout=_checks_json(payload))
    monkeypatch.setattr("asyncio.create_subprocess_exec", registry.create_subprocess_exec)
    host = GitHubGitHost(project_dir)
    checks = await host.get_pr_checks(42)
    assert checks.all_passed is True
    assert checks.failed == ()


async def test_get_pr_checks_failed_returns_subset_with_links(
    registry: FakeSubprocessRegistry,
    project_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = [
        {"name": "lint", "state": "SUCCESS", "link": "https://pass.com", "description": ""},
        {"name": "tests", "state": "FAILURE", "link": "https://fail.com", "description": ""},
    ]
    registry.expect(_CHECKS_ARGV, stdout=_checks_json(payload))
    monkeypatch.setattr("asyncio.create_subprocess_exec", registry.create_subprocess_exec)
    host = GitHubGitHost(project_dir)
    checks = await host.get_pr_checks(42)
    assert len(checks.failed) == 1
    assert checks.failed[0].name == "tests"
    assert checks.failed[0].link == "https://fail.com"


async def test_get_pr_checks_pending_detection(
    registry: FakeSubprocessRegistry,
    project_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = [
        {"name": "build", "state": "SUCCESS", "link": "", "description": ""},
        {"name": "deploy", "state": "QUEUED", "link": "", "description": ""},
    ]
    registry.expect(_CHECKS_ARGV, stdout=_checks_json(payload))
    monkeypatch.setattr("asyncio.create_subprocess_exec", registry.create_subprocess_exec)
    host = GitHubGitHost(project_dir)
    checks = await host.get_pr_checks(42)
    assert checks.pending is True
    assert checks.all_passed is False


async def test_get_pr_checks_raises_host_error_on_gh_failure(
    registry: FakeSubprocessRegistry,
    project_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry.expect(_CHECKS_ARGV, returncode=1, stderr=b"gh: not found")
    monkeypatch.setattr("asyncio.create_subprocess_exec", registry.create_subprocess_exec)
    host = GitHubGitHost(project_dir)
    with pytest.raises(HostError):
        await host.get_pr_checks(42)


async def test_comment_on_pr_sends_correct_argv(
    registry: FakeSubprocessRegistry,
    project_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    argv = ("gh", "pr", "comment", "7", "--body-file", "-")
    proc = registry.expect(argv)
    monkeypatch.setattr("asyncio.create_subprocess_exec", registry.create_subprocess_exec)
    host = GitHubGitHost(project_dir)
    await host.comment_on_pr(7, "hello from cog")
    assert argv in registry.calls
    assert proc.received_stdin == b"hello from cog"


async def test_comment_on_pr_raises_host_error_on_failure(
    registry: FakeSubprocessRegistry,
    project_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    argv = ("gh", "pr", "comment", "7", "--body-file", "-")
    registry.expect(argv, returncode=1, stderr=b"error")
    monkeypatch.setattr("asyncio.create_subprocess_exec", registry.create_subprocess_exec)
    host = GitHubGitHost(project_dir)
    with pytest.raises(HostError):
        await host.comment_on_pr(7, "body")
