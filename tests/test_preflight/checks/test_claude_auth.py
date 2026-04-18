from pathlib import Path

from tests.fakes import FakeSubprocessRegistry


async def test_env_key_set(tmp_path: Path) -> None:
    from cog.checks import CheckClaudeAuth

    result = await CheckClaudeAuth(_env={"ANTHROPIC_API_KEY": "sk-test"}).run(tmp_path)
    assert result.ok is True
    assert result.level == "warning"


async def test_env_unset_security_success(tmp_path: Path) -> None:
    from cog.checks import CheckClaudeAuth

    reg = FakeSubprocessRegistry()
    reg.expect(
        ("security", "find-generic-password", "-s", "Claude Code-credentials", "-w"),
        returncode=0,
    )
    result = await CheckClaudeAuth(
        _env={},
        _which=lambda _: "/usr/bin/security",
        _create_subprocess=reg.create_subprocess_exec,
    ).run(tmp_path)
    assert result.ok is True
    assert result.level == "warning"


async def test_env_unset_security_fail(tmp_path: Path) -> None:
    from cog.checks import CheckClaudeAuth

    reg = FakeSubprocessRegistry()
    reg.expect(
        ("security", "find-generic-password", "-s", "Claude Code-credentials", "-w"),
        returncode=44,
    )
    result = await CheckClaudeAuth(
        _env={},
        _which=lambda _: "/usr/bin/security",
        _create_subprocess=reg.create_subprocess_exec,
    ).run(tmp_path)
    assert result.ok is False
    assert result.level == "warning"
    assert "ANTHROPIC_API_KEY" in result.message


async def test_env_unset_security_missing(tmp_path: Path) -> None:
    from cog.checks import CheckClaudeAuth

    result = await CheckClaudeAuth(
        _env={},
        _which=lambda _: None,
    ).run(tmp_path)
    assert result.ok is False
    assert result.level == "warning"
    assert "ANTHROPIC_API_KEY" in result.message
