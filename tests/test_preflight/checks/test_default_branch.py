from pathlib import Path

from tests.fakes import FakeSubprocessRegistry


def _reg(
    origin_returncode: int = 0,
    origin_stdout: bytes = b"origin/main\n",
    head_returncode: int = 0,
    head_stdout: bytes = b"main\n",
) -> FakeSubprocessRegistry:
    reg = FakeSubprocessRegistry()
    reg.expect(
        ("git", "symbolic-ref", "--short", "refs/remotes/origin/HEAD"),
        returncode=origin_returncode,
        stdout=origin_stdout,
    )
    if origin_returncode == 0:
        reg.expect(
            ("git", "symbolic-ref", "--short", "HEAD"),
            returncode=head_returncode,
            stdout=head_stdout,
        )
    return reg


async def test_matches(tmp_path: Path) -> None:
    from cog.checks import CheckDefaultBranch

    reg = _reg()
    result = await CheckDefaultBranch(_create_subprocess=reg.create_subprocess_exec).run(tmp_path)
    assert result.ok is True
    assert "main" in result.message


async def test_mismatches(tmp_path: Path) -> None:
    from cog.checks import CheckDefaultBranch

    reg = _reg(head_stdout=b"feature-branch\n")
    result = await CheckDefaultBranch(_create_subprocess=reg.create_subprocess_exec).run(tmp_path)
    assert result.ok is False
    assert "feature-branch" in result.message
    assert "main" in result.message


async def test_origin_head_missing(tmp_path: Path) -> None:
    from cog.checks import CheckDefaultBranch

    reg = _reg(origin_returncode=128)
    result = await CheckDefaultBranch(_create_subprocess=reg.create_subprocess_exec).run(tmp_path)
    assert result.ok is False
    assert "git remote set-head origin --auto" in result.message


async def test_detached_head(tmp_path: Path) -> None:
    from cog.checks import CheckDefaultBranch

    reg = _reg(head_returncode=128)
    result = await CheckDefaultBranch(_create_subprocess=reg.create_subprocess_exec).run(tmp_path)
    assert result.ok is False
    assert "detached HEAD" in result.message
