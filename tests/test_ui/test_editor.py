"""Tests for suspend_and_edit editor helper."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


def _make_app() -> MagicMock:
    app = MagicMock()
    app.suspend.return_value.__enter__ = MagicMock(return_value=None)
    app.suspend.return_value.__exit__ = MagicMock(return_value=False)
    return app


async def _edit(app, initial_text, tmp_dir, *, editor_changes: bool = True) -> str | None:
    """Run suspend_and_edit with a fake subprocess that optionally modifies the file."""
    from cog.ui.editor import suspend_and_edit

    def fake_run(cmd, check=False):
        if editor_changes:
            # simulate editor saving: find the path in cmd and write to it
            path = Path(cmd[-1])
            path.write_text(initial_text + " [edited]", encoding="utf-8")
            # Force mtime change
            current = path.stat().st_mtime
            os.utime(path, (current + 1, current + 1))

    with (
        patch("cog.ui.editor.subprocess.run", side_effect=fake_run),
        patch("cog.ui.editor._find_editor", return_value="nano"),
    ):
        return await suspend_and_edit(app, initial_text, tmp_dir)


@pytest.mark.asyncio
async def test_suspend_and_edit_returns_edited_text_when_saved(tmp_path):
    app = _make_app()
    result = await _edit(app, "hello", tmp_path, editor_changes=True)
    assert result is not None
    assert "edited" in result


@pytest.mark.asyncio
async def test_suspend_and_edit_returns_none_on_exit_without_save(tmp_path):
    app = _make_app()
    result = await _edit(app, "hello", tmp_path, editor_changes=False)
    assert result is None


@pytest.mark.asyncio
async def test_suspend_and_edit_falls_back_to_nano_when_editor_unset(tmp_path):
    from cog.ui.editor import suspend_and_edit

    app = _make_app()
    called_with: list[list[str]] = []

    def fake_run(cmd, check=False):
        called_with.append(list(cmd))

    with (
        patch.dict(os.environ, {}, clear=True),
        patch("cog.ui.editor.shutil.which", side_effect=lambda x: x if x == "nano" else None),
        patch("cog.ui.editor.subprocess.run", side_effect=fake_run),
    ):
        # Remove EDITOR from env
        os.environ.pop("EDITOR", None)
        await suspend_and_edit(app, "text", tmp_path)

    assert called_with and called_with[0][0] == "nano"


@pytest.mark.asyncio
async def test_suspend_and_edit_falls_back_to_vi_when_editor_and_nano_missing(tmp_path):
    from cog.ui.editor import suspend_and_edit

    app = _make_app()
    called_with: list[list[str]] = []

    def fake_run(cmd, check=False):
        called_with.append(list(cmd))

    def which(name):
        return name if name == "vi" else None

    with (
        patch.dict(os.environ, {}, clear=True),
        patch("cog.ui.editor.shutil.which", side_effect=which),
        patch("cog.ui.editor.subprocess.run", side_effect=fake_run),
    ):
        os.environ.pop("EDITOR", None)
        await suspend_and_edit(app, "text", tmp_path)

    assert called_with and called_with[0][0] == "vi"


@pytest.mark.asyncio
async def test_suspend_and_edit_raises_when_no_editor_available(tmp_path):
    from cog.ui.editor import suspend_and_edit

    app = _make_app()

    with (
        patch.dict(os.environ, {}, clear=True),
        patch("cog.ui.editor.shutil.which", return_value=None),
    ):
        os.environ.pop("EDITOR", None)
        with pytest.raises(RuntimeError, match="No editor found"):
            await suspend_and_edit(app, "text", tmp_path)


@pytest.mark.asyncio
async def test_suspend_and_edit_writes_tmp_file_in_ctx_tmp_dir(tmp_path):
    from cog.ui.editor import suspend_and_edit

    app = _make_app()
    written_paths: list[str] = []

    def fake_run(cmd, check=False):
        written_paths.append(cmd[-1])

    with (
        patch("cog.ui.editor._find_editor", return_value="nano"),
        patch("cog.ui.editor.subprocess.run", side_effect=fake_run),
    ):
        await suspend_and_edit(app, "hello", tmp_path)

    assert written_paths
    assert str(tmp_path) in written_paths[0]
