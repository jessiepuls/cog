"""suspend_and_edit — suspend Textual and drop user into $EDITOR."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from textual.app import App


def _find_editor() -> str:
    editor = os.environ.get("EDITOR")
    if editor and shutil.which(editor):
        return editor
    for fallback in ("nano", "vi"):
        if shutil.which(fallback):
            return fallback
    raise RuntimeError(
        "No editor found. Set $EDITOR to your preferred editor (e.g. export EDITOR=vim)."
    )


async def suspend_and_edit(
    app: App,
    initial_text: str,
    tmp_dir: Path,
    suffix: str = ".md",
) -> str | None:
    """Suspend Textual, drop user to $EDITOR on a tmp file seeded with initial_text.

    Returns the edited text if the user saved, or None if the editor exited
    without saving (tmp file unchanged). Falls back through $EDITOR → nano → vi.
    Raises if none of those resolve to a runnable binary.
    """
    editor = _find_editor()
    fd, path_str = tempfile.mkstemp(suffix=suffix, dir=tmp_dir)
    path = Path(path_str)
    os.close(fd)
    path.write_text(initial_text, encoding="utf-8")
    mtime_before = path.stat().st_mtime
    with app.suspend():
        subprocess.run([editor, str(path)], check=False)
    mtime_after = path.stat().st_mtime
    if mtime_after == mtime_before:
        return None
    return path.read_text(encoding="utf-8")
