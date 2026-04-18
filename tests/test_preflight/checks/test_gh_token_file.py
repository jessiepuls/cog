from pathlib import Path

from cog.checks import CheckGhTokenFile


async def test_file_present_with_oauth_token(tmp_path: Path) -> None:
    hosts = tmp_path / ".config" / "gh"
    hosts.mkdir(parents=True)
    (hosts / "hosts.yml").write_text("github.com:\n  oauth_token: abc123\n")
    result = await CheckGhTokenFile(_home_dir=tmp_path).run(tmp_path)
    assert result.ok is True


async def test_file_present_without_oauth_token(tmp_path: Path) -> None:
    hosts = tmp_path / ".config" / "gh"
    hosts.mkdir(parents=True)
    (hosts / "hosts.yml").write_text("github.com:\n  user: someone\n")
    result = await CheckGhTokenFile(_home_dir=tmp_path).run(tmp_path)
    assert result.ok is False
    assert "insecure-storage" in result.message


async def test_file_absent(tmp_path: Path) -> None:
    result = await CheckGhTokenFile(_home_dir=tmp_path).run(tmp_path)
    assert result.ok is False
    assert "insecure-storage" in result.message
