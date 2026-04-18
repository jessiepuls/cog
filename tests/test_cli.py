import re

from typer.testing import CliRunner

from cog import __version__
from cog.cli import app

runner = CliRunner()

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _plain(output: str) -> str:
    return _ANSI_RE.sub("", output)


def test_version():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert f"cog {__version__}" in _plain(result.output)


def test_help():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "--version" in _plain(result.output)
