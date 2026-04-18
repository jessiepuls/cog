from pathlib import Path

import pytest

from tests.fakes import FakeSubprocessRegistry

FIXTURES = Path(__file__).parent.parent / "fixtures" / "gh"


def load_fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


@pytest.fixture
def registry() -> FakeSubprocessRegistry:
    return FakeSubprocessRegistry()


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    return tmp_path
