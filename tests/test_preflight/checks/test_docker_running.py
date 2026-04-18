from pathlib import Path

from tests.fakes import FakeSubprocessRegistry


async def test_docker_running(tmp_path: Path) -> None:
    from cog.checks import CheckDockerRunning

    reg = FakeSubprocessRegistry()
    reg.expect(("docker", "info"), returncode=0)
    result = await CheckDockerRunning(_create_subprocess=reg.create_subprocess_exec).run(tmp_path)
    assert result.ok is True
    assert result.level == "error"


async def test_docker_not_running(tmp_path: Path) -> None:
    from cog.checks import CheckDockerRunning

    reg = FakeSubprocessRegistry()
    reg.expect(("docker", "info"), returncode=1)
    result = await CheckDockerRunning(_create_subprocess=reg.create_subprocess_exec).run(tmp_path)
    assert result.ok is False
    assert "docker daemon not running" in result.message
