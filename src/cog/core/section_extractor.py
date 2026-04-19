from __future__ import annotations

import re
from collections.abc import Iterable

_SECTION_RE = re.compile(
    r"^###\s+(.+?)\s*\n(.*?)(?=\n###\s|\Z)",
    re.MULTILINE | re.DOTALL,
)


def extract_sections(message: str, section_names: Iterable[str]) -> dict[str, str]:
    """Pull named markdown sections (### Name) from a claude final message.

    Returns dict keyed by section name lowercased + underscored
    ("Key changes" → "key_changes"). Missing sections absent.
    """
    names_lower = {n.lower() for n in section_names}
    sections: dict[str, str] = {}
    for match in _SECTION_RE.finditer(message):
        raw_name = match.group(1).strip()
        if raw_name.lower() in names_lower:
            key = raw_name.lower().replace(" ", "_")
            sections[key] = match.group(2).strip()
    return sections
