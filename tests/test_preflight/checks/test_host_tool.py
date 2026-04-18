from pathlib import Path

from cog.checks import CheckHostTool


async def test_binary_found(tmp_path: Path) -> None:
    check = CheckHostTool("git", _which=lambda _: "/usr/bin/git")
    result = await check.run(tmp_path)
    assert result.ok is True
    assert result.level == "error"
    assert "git" in result.message


async def test_binary_missing(tmp_path: Path) -> None:
    check = CheckHostTool("git", _which=lambda _: None)
    result = await check.run(tmp_path)
    assert result.ok is False
    assert result.level == "error"
    assert "git is not installed" in result.message


async def test_name_includes_binary(tmp_path: Path) -> None:
    check = CheckHostTool("docker", _which=lambda _: None)
    assert check.name == "host_tool.docker"
