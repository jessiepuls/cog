import subprocess

import pytest

from cog.core.context import ExecutionContext
from tests.fakes import EchoRunner, InMemoryStateCache


@pytest.fixture
def tmp_project_dir(tmp_path):
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    return tmp_path


@pytest.fixture
def in_memory_state_cache():
    return InMemoryStateCache()


@pytest.fixture
def echo_runner():
    return EchoRunner()


@pytest.fixture
def ctx_factory(tmp_project_dir, in_memory_state_cache):
    def _make(**overrides):
        params = {
            "project_dir": tmp_project_dir,
            "tmp_dir": tmp_project_dir / "tmp",
            "state_cache": in_memory_state_cache,
            "headless": True,
        }
        params.update(overrides)
        return ExecutionContext(**params)

    return _make
