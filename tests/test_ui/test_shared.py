"""Direct unit tests for tool_preview() in isolation."""

import pytest

from cog.ui.widgets._shared import tool_preview
from tests.fakes import make_tool_event


def test_tool_preview_bash_command() -> None:
    event = make_tool_event("Bash", command="ls -la")
    assert tool_preview(event) == "ls -la"


def test_tool_preview_read_file_path() -> None:
    event = make_tool_event("Read", file_path="/src/main.py")
    assert tool_preview(event) == "/src/main.py"


def test_tool_preview_write_file_path() -> None:
    event = make_tool_event("Write", file_path="/out/result.txt")
    assert tool_preview(event) == "/out/result.txt"


def test_tool_preview_edit_file_path() -> None:
    event = make_tool_event("Edit", file_path="/src/foo.py", old_string="x", new_string="y")
    assert tool_preview(event) == "/src/foo.py"


def test_tool_preview_glob_pattern() -> None:
    event = make_tool_event("Glob", pattern="**/*.py")
    assert tool_preview(event) == "**/*.py"


def test_tool_preview_grep_pattern() -> None:
    event = make_tool_event("Grep", pattern="def foo")
    assert tool_preview(event) == "def foo"


def test_tool_preview_agent_prefers_description_over_prompt() -> None:
    event = make_tool_event("Agent", description="explore widgets", prompt="search for things")
    assert tool_preview(event) == "explore widgets"


def test_tool_preview_agent_falls_back_to_prompt_when_no_description() -> None:
    event = make_tool_event("Agent", prompt="search for things")
    assert tool_preview(event) == "search for things"


def test_tool_preview_task_prefers_description() -> None:
    event = make_tool_event("Task", description="run tests", prompt="execute the suite")
    assert tool_preview(event) == "run tests"


def test_tool_preview_toolsearch_query() -> None:
    event = make_tool_event("ToolSearch", query="select:Read,Edit")
    assert tool_preview(event) == "select:Read,Edit"


def test_tool_preview_todowrite_shows_item_count() -> None:
    event = make_tool_event("TodoWrite", todos=[{"id": 1}, {"id": 2}, {"id": 3}])
    assert tool_preview(event) == "(3 items)"


def test_tool_preview_todowrite_empty_list() -> None:
    event = make_tool_event("TodoWrite", todos=[])
    assert tool_preview(event) == "(0 items)"


def test_tool_preview_unknown_tool_uses_first_string_value_fallback() -> None:
    event = make_tool_event("FutureTool", my_param="some value")
    assert tool_preview(event) == "some value"


def test_tool_preview_unknown_tool_with_only_non_string_values_renders_empty() -> None:
    event = make_tool_event("FutureTool", count=42, flag=True)
    assert tool_preview(event) == ""


def test_tool_preview_truncates_at_100_chars_with_ellipsis() -> None:
    long_cmd = "x" * 101
    event = make_tool_event("Bash", command=long_cmd)
    result = tool_preview(event)
    assert len(result) == 100
    assert result.endswith("…")
    assert result[:99] == "x" * 99


def test_tool_preview_at_exactly_100_chars_is_not_truncated() -> None:
    exact_cmd = "x" * 100
    event = make_tool_event("Bash", command=exact_cmd)
    result = tool_preview(event)
    assert result == exact_cmd
    assert len(result) == 100


@pytest.mark.parametrize(
    "tool,kwargs,expected",
    [
        ("Bash", {"command": "echo hi"}, "echo hi"),
        ("Read", {"file_path": "/a.py"}, "/a.py"),
        ("Write", {"file_path": "/b.py"}, "/b.py"),
        ("Edit", {"file_path": "/c.py"}, "/c.py"),
        ("Glob", {"pattern": "*.py"}, "*.py"),
        ("Grep", {"pattern": "foo"}, "foo"),
        ("Agent", {"description": "desc", "prompt": "p"}, "desc"),
        ("Agent", {"prompt": "fallback"}, "fallback"),
        ("ToolSearch", {"query": "search"}, "search"),
        ("TodoWrite", {"todos": [1, 2]}, "(2 items)"),
        ("Unknown", {"my_key": "my_value"}, "my_value"),
        ("Unknown", {"n": 1}, ""),
    ],
)
def test_tool_preview_parametrized(tool: str, kwargs: dict, expected: str) -> None:
    event = make_tool_event(tool, **kwargs)
    assert tool_preview(event) == expected
