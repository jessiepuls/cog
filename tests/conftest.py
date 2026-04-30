import asyncio
import atexit
import subprocess

import pytest

from cog.core.context import ExecutionContext
from tests.fakes import EchoRunner, InMemoryStateCache

# pytest-asyncio 1.3.0's `_temporary_event_loop_policy` calls
# `asyncio.get_event_loop()` once per test setup; on Python 3.12 with the
# default policy this auto-creates a fresh `_UnixSelectorEventLoop` (with a
# self-pipe socketpair) every test and never closes it. With many async tests
# the leaked loops/sockets eventually trip a gc'd `ResourceWarning` mid-suite,
# which `filterwarnings = ["error"]` promotes to a failure on whichever test
# happens to be running gc. Installing a session-wide current loop here makes
# `get_event_loop()` return this one instead of creating per-test.
_session_loop = asyncio.new_event_loop()
asyncio.set_event_loop(_session_loop)
atexit.register(_session_loop.close)


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
