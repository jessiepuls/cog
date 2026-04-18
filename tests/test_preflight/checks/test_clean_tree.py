from pathlib import Path

from tests.fakes import FakeSubprocessRegistry


def _reg(unstaged: int = 0, staged: int = 0, status_out: bytes = b"") -> FakeSubprocessRegistry:
    reg = FakeSubprocessRegistry()
    reg.expect(("git", "diff", "--quiet"), returncode=unstaged)
    reg.expect(("git", "diff", "--cached", "--quiet"), returncode=staged)
    reg.expect(
        ("git", "status", "--porcelain", "--untracked-files=normal"),
        returncode=0,
        stdout=status_out,
    )
    return reg


async def test_clean(tmp_path: Path) -> None:
    from cog.checks import CheckCleanTree

    result = await CheckCleanTree(_create_subprocess=_reg().create_subprocess_exec).run(tmp_path)
    assert result.ok is True
    assert "clean" in result.message


async def test_unstaged_changes(tmp_path: Path) -> None:
    from cog.checks import CheckCleanTree

    reg = _reg(unstaged=1)
    result = await CheckCleanTree(_create_subprocess=reg.create_subprocess_exec).run(tmp_path)
    assert result.ok is False
    assert "unstaged changes" in result.message
    assert "git stash or commit first" in result.message


async def test_staged_changes(tmp_path: Path) -> None:
    from cog.checks import CheckCleanTree

    reg = _reg(staged=1)
    result = await CheckCleanTree(_create_subprocess=reg.create_subprocess_exec).run(tmp_path)
    assert result.ok is False
    assert "staged changes" in result.message


async def test_untracked_files(tmp_path: Path) -> None:
    from cog.checks import CheckCleanTree

    reg = _reg(status_out=b"?? newfile.txt\n?? another.txt\n")
    result = await CheckCleanTree(_create_subprocess=reg.create_subprocess_exec).run(tmp_path)
    assert result.ok is False
    assert "untracked" in result.message
    assert "2" in result.message
