from pathlib import Path

from tests.fakes import FakeSubprocessRegistry


async def test_origin_configured(tmp_path: Path) -> None:
    from cog.checks import CheckOriginRemote

    reg = FakeSubprocessRegistry()
    reg.expect(
        ("git", "remote", "get-url", "origin"), returncode=0, stdout=b"git@github.com:org/repo\n"
    )  # noqa: E501
    result = await CheckOriginRemote(_create_subprocess=reg.create_subprocess_exec).run(tmp_path)
    assert result.ok is True


async def test_origin_missing(tmp_path: Path) -> None:
    from cog.checks import CheckOriginRemote

    reg = FakeSubprocessRegistry()
    reg.expect(("git", "remote", "get-url", "origin"), returncode=2)
    result = await CheckOriginRemote(_create_subprocess=reg.create_subprocess_exec).run(tmp_path)
    assert result.ok is False
    assert "no 'origin' remote configured" in result.message
