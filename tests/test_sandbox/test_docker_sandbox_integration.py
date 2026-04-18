"""Integration tests for DockerSandbox — require a real Docker daemon.

Set COG_INTEGRATION_TESTS=1 to run. These are intentionally excluded from CI;
run manually before releases to confirm the fat image builds and works end-to-end.
"""

import os
import subprocess

import pytest

from cog.runners.docker_sandbox import DockerSandbox

pytestmark = pytest.mark.skipif(
    not os.environ.get("COG_INTEGRATION_TESTS"),
    reason="set COG_INTEGRATION_TESTS=1 to run (requires docker)",
)


async def test_real_image_builds() -> None:
    sandbox = DockerSandbox()
    await sandbox.prepare()
    assert sandbox._built


async def test_real_smoke_test_passes() -> None:
    sandbox = DockerSandbox()
    await sandbox.prepare()
    await sandbox.smoke_test()  # raises SandboxError if any tool is missing


async def test_real_wrap_and_exec_runs_true() -> None:
    sandbox = DockerSandbox()
    await sandbox.prepare()
    argv = sandbox.wrap_argv(["true"])
    result = subprocess.run(argv, check=False)
    assert result.returncode == 0
