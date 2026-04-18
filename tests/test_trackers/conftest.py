from pathlib import Path

import pytest

from tests.fakes import FakeSubprocessRegistry

FIXTURES = Path(__file__).parent.parent / "fixtures" / "gh"


def load_fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def repo_fixture() -> bytes:
    return load_fixture("repo_view_nameWithOwner.json")


@pytest.fixture
def registry() -> FakeSubprocessRegistry:
    return FakeSubprocessRegistry()


@pytest.fixture
def tracker_dir(tmp_path: Path) -> Path:
    return tmp_path


def register_repo(reg: FakeSubprocessRegistry) -> None:
    reg.expect(
        ("gh", "repo", "view", "--json", "nameWithOwner"),
        stdout=repo_fixture(),
    )
