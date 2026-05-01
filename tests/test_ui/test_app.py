"""Tests for CogApp initialization behavior."""

from pathlib import Path

import pytest
from textual.screen import Screen

from cog.ui.app import CogApp


class _EmptyScreen(Screen):
    def compose(self):
        return iter([])


@pytest.mark.parametrize(
    "project_dir, expected",
    [
        (Path("/tmp/some-name"), "some-name"),
        (Path("."), Path.cwd().name),
    ],
)
def test_sub_title_set_from_project_dir(project_dir: Path, expected: str) -> None:
    app = CogApp(_EmptyScreen(), project_dir)
    assert app.sub_title == expected
