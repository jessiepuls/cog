from pathlib import Path

from tests.fakes import FakeSubprocessRegistry


async def test_inside_git_repo(tmp_path: Path) -> None:
    from cog.checks import CheckGitRepo

    reg = FakeSubprocessRegistry()
    reg.expect(("git", "rev-parse", "--is-inside-work-tree"), returncode=0)
    result = await CheckGitRepo(_create_subprocess=reg.create_subprocess_exec).run(tmp_path)
    assert result.ok is True
    assert result.level == "error"


async def test_not_inside_git_repo(tmp_path: Path) -> None:
    from cog.checks import CheckGitRepo

    reg = FakeSubprocessRegistry()
    reg.expect(("git", "rev-parse", "--is-inside-work-tree"), returncode=128)
    result = await CheckGitRepo(_create_subprocess=reg.create_subprocess_exec).run(tmp_path)
    assert result.ok is False
    assert "not inside a git repository" in result.message
