from datetime import datetime
from pathlib import Path

import pytest

from cog.core.item import Item
from cog.hosts.github import GitHubGitHost
from tests.fakes import FakeSubprocessRegistry

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "gh"


def make_item(item_id: str) -> Item:
    return Item(
        tracker_id="github/jessiepuls/cog",
        item_id=item_id,
        title="Test issue",
        body="",
        labels=(),
        comments=(),
        updated_at=datetime(2024, 1, 1),
        url=f"https://github.com/jessiepuls/cog/issues/{item_id}",
    )


@pytest.fixture
def project_dir(tmp_path):
    return tmp_path


async def test_search_query_compound_shape(project_dir):
    reg = FakeSubprocessRegistry()
    reg.push(stdout=b"[]")
    host = GitHubGitHost(project_dir)
    with reg.patch("cog.hosts.github"):
        await host.get_open_prs_mentioning_item(make_item("42"))
    argv = reg.calls[0].argv
    search_idx = argv.index("--search")
    assert argv[search_idx + 1] == '"Closes #42" OR "Fixes #42" OR "Resolves #42"'


async def test_search_state_filter_open(project_dir):
    reg = FakeSubprocessRegistry()
    reg.push(stdout=b"[]")
    host = GitHubGitHost(project_dir)
    with reg.patch("cog.hosts.github"):
        await host.get_open_prs_mentioning_item(make_item("42"))
    argv = reg.calls[0].argv
    state_idx = argv.index("--state")
    assert argv[state_idx + 1] == "open"


async def test_multiple_matches_returned(project_dir):
    reg = FakeSubprocessRegistry()
    reg.push(stdout=(FIXTURES_DIR / "pr_list_mentioning_item.json").read_bytes())
    host = GitHubGitHost(project_dir)
    with reg.patch("cog.hosts.github"):
        prs = await host.get_open_prs_mentioning_item(make_item("42"))
    assert len(prs) == 2
    assert all(pr.state == "open" for pr in prs)
    assert prs[0].number == 10
    assert prs[1].number == 11
