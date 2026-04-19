from datetime import UTC, datetime
from pathlib import Path

import pytest

from cog.core.item import Item
from cog.hosts.github import GitHubGitHost
from tests.fakes import FakeSubprocessRegistry
from tests.test_hosts.conftest import load_fixture


def make_item(item_id: str) -> Item:
    return Item(
        tracker_id="github/jessiepuls/cog",
        item_id=item_id,
        title="Test issue",
        body="",
        labels=(),
        comments=(),
        state="open",
        created_at=datetime(2024, 1, 1, tzinfo=UTC),
        updated_at=datetime(2024, 1, 1, tzinfo=UTC),
        url=f"https://github.com/jessiepuls/cog/issues/{item_id}",
    )


def search_argv(item_id: str) -> tuple[str, ...]:
    query = f'"Closes #{item_id}" OR "Fixes #{item_id}" OR "Resolves #{item_id}"'
    return (
        "gh",
        "pr",
        "list",
        "--search",
        query,
        "--state",
        "open",
        "--json",
        "number,url,state,body,headRefName",
    )


async def test_search_query_compound_shape(
    registry: FakeSubprocessRegistry,
    project_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    argv = search_argv("42")
    registry.expect(argv, stdout=b"[]")
    monkeypatch.setattr("asyncio.create_subprocess_exec", registry.create_subprocess_exec)
    host = GitHubGitHost(project_dir)
    await host.get_open_prs_mentioning_item(make_item("42"))
    call = registry.calls[0]
    search_idx = call.index("--search")
    assert call[search_idx + 1] == '"Closes #42" OR "Fixes #42" OR "Resolves #42"'


async def test_search_state_filter_open(
    registry: FakeSubprocessRegistry,
    project_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry.expect(search_argv("42"), stdout=b"[]")
    monkeypatch.setattr("asyncio.create_subprocess_exec", registry.create_subprocess_exec)
    host = GitHubGitHost(project_dir)
    await host.get_open_prs_mentioning_item(make_item("42"))
    call = registry.calls[0]
    state_idx = call.index("--state")
    assert call[state_idx + 1] == "open"


async def test_multiple_matches_returned(
    registry: FakeSubprocessRegistry,
    project_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry.expect(search_argv("42"), stdout=load_fixture("pr_list_mentioning_item.json"))
    monkeypatch.setattr("asyncio.create_subprocess_exec", registry.create_subprocess_exec)
    host = GitHubGitHost(project_dir)
    prs = await host.get_open_prs_mentioning_item(make_item("42"))
    assert len(prs) == 2
    assert all(pr.state == "open" for pr in prs)
    assert prs[0].number == 10
    assert prs[1].number == 11
