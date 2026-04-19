"""Shared rendering helpers for widgets that consume claude stream events."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from cog.core.runner import ToolUseEvent

_TRUNCATE_CAP = 100


def tool_preview(event: ToolUseEvent) -> str:
    """Short human-readable summary of a ToolUseEvent's input.

    Empty string if no useful preview is extractable.
    """
    if event.tool == "TodoWrite":
        todos = event.input.get("todos")
        if isinstance(todos, list):
            return f"({len(todos)} items)"
        return ""

    raw = (
        event.input.get("command")
        or event.input.get("file_path")
        or event.input.get("pattern")
        or event.input.get("description")
        or event.input.get("prompt")
        or event.input.get("query")
        or _first_string_value(event.input)
        or ""
    )
    if not raw:
        return ""
    if len(raw) <= _TRUNCATE_CAP:
        return raw
    return raw[: _TRUNCATE_CAP - 1] + "…"


def _first_string_value(d: Mapping[str, Any]) -> str:
    for v in d.values():
        if isinstance(v, str) and v:
            return v
    return ""
