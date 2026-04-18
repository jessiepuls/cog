from pathlib import Path

from cog.state_paths import project_slug, project_state_dir


def _p(name: str) -> Path:
    """Build a Path whose .name is exactly `name` by using a fake parent."""
    return Path("/fake") / name


def test_slug_lowercases():
    assert project_slug(_p("MyProject")) == "myproject"


def test_slug_replaces_special_chars():
    # "My Project!" → "my-project-"
    assert project_slug(_p("My Project!")) == "my-project-"


def test_slug_multiple_special_chars():
    # Characters a, @, b, #, c → a-b-c
    assert project_slug(_p("a@b#c")) == "a-b-c"


def test_slug_empty_fallback():
    # Path("/").name == "" → fallback to "project"
    assert project_slug(Path("/")) == "project"


def test_slug_nonempty_dashes_not_fallback():
    # "---" is non-empty after regex sub → preserved as-is (no fallback)
    assert project_slug(_p("!!!")) == "---"


def test_state_dir_default(monkeypatch):
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    result = project_state_dir(_p("my-project"))
    assert result == Path.home() / ".local" / "state" / "cog" / "my-project"


def test_state_dir_respects_xdg_state_home(tmp_path, monkeypatch):
    custom_base = str(tmp_path / "custom_state")
    monkeypatch.setenv("XDG_STATE_HOME", custom_base)
    result = project_state_dir(_p("my-project"))
    assert result == Path(custom_base) / "cog" / "my-project"
