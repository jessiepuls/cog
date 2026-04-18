from pathlib import Path

from tests.fakes import FakeSubprocessRegistry


async def test_gh_auth_ok(tmp_path: Path) -> None:
    from cog.checks import CheckGhAuth

    reg = FakeSubprocessRegistry()
    reg.expect(("gh", "auth", "status"), returncode=0)
    result = await CheckGhAuth(_create_subprocess=reg.create_subprocess_exec).run(tmp_path)
    assert result.ok is True
    assert result.level == "error"


async def test_gh_auth_fail(tmp_path: Path) -> None:
    from cog.checks import CheckGhAuth

    reg = FakeSubprocessRegistry()
    reg.expect(("gh", "auth", "status"), returncode=1)
    result = await CheckGhAuth(_create_subprocess=reg.create_subprocess_exec).run(tmp_path)
    assert result.ok is False
    assert "gh auth login" in result.message
