"""Typed-query filter for the Issues view (#200).

Provides parse_query(), apply_parsed(), and FilterSuggester for the
single-input filter bar that replaced the chip-row design.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from textual.suggester import Suggester

from cog.core.item import Item


@dataclass(frozen=True)
class ParsedQuery:
    """Structured result of parse_query().

    label_groups: each element is an OR-group (frozenset of names); all groups
        are AND-combined. So label:bug,docs means one OR-group {bug, docs},
        while label:bug label:docs means two OR-groups, each a singleton.
    assignee_groups: same structure for assignee constraints.
    state_set: frozenset of states to include; None means no constraint.
    barewords: plain bareword tokens, AND-combined title-substring matches.
    quoted_barewords: quoted bareword tokens, AND-combined, no integer shortcut.
    """

    state_set: frozenset[str] | None = None
    label_groups: tuple[frozenset[str], ...] = ()
    assignee_groups: tuple[frozenset[str], ...] = ()
    barewords: tuple[str, ...] = ()
    quoted_barewords: tuple[str, ...] = ()


_VALID_STATES = {"open", "closed", "all"}


def _tokenize(text: str) -> list[str]:
    """Split text into whitespace-separated tokens, respecting double quotes."""
    tokens: list[str] = []
    current: list[str] = []
    in_q = False
    i = 0
    while i < len(text):
        c = text[i]
        if c == "\\" and in_q and i + 1 < len(text):
            current.append(text[i + 1])
            i += 2
            continue
        if c == '"':
            in_q = not in_q
            current.append(c)
        elif c == " " and not in_q:
            if current:
                tokens.append("".join(current))
                current = []
        else:
            current.append(c)
        i += 1
    if current:
        tokens.append("".join(current))
    return tokens


def _last_unquoted(text: str, char: str) -> int:
    """Return index of last occurrence of char outside double quotes, or -1."""
    in_q = False
    last = -1
    for i, c in enumerate(text):
        if c == '"':
            in_q = not in_q
        elif c == char and not in_q:
            last = i
    return last


def _split_values(s: str) -> list[str]:
    """Split a comma-list of values, quote-aware. Returns non-empty stripped values."""
    values: list[str] = []
    current: list[str] = []
    in_q = False
    for c in s:
        if c == '"':
            in_q = not in_q
            current.append(c)
        elif c == "," and not in_q:
            v = "".join(current).strip()
            if v:
                values.append(v)
            current = []
        else:
            current.append(c)
    v = "".join(current).strip()
    if v:
        values.append(v)
    return values


def _unquote(s: str) -> str:
    """Remove surrounding double quotes if present."""
    if len(s) >= 2 and s.startswith('"') and s.endswith('"'):
        return s[1:-1]
    return s


def _is_complete_value(v: str) -> bool:
    """Return False if the value is an incomplete quoted string (tolerant parse)."""
    if v.startswith('"'):
        return v.endswith('"') and len(v) >= 2
    return bool(v)


def parse_query(text: str) -> ParsedQuery:
    """Parse a typed filter query into a structured form.

    Tolerant: unknown or incomplete tokens contribute no constraint.
    """
    tokens = _tokenize(text.strip())
    state_parts: list[str] = []
    label_groups: list[frozenset[str]] = []
    assignee_groups: list[frozenset[str]] = []
    barewords: list[str] = []
    quoted_barewords: list[str] = []

    for token in tokens:
        # Quoted bareword: starts and ends with " (no key prefix)
        if token.startswith('"') and token.endswith('"') and len(token) >= 2:
            quoted_barewords.append(_unquote(token))
            continue

        colon_idx = token.find(":")
        if colon_idx > 0:
            key = token[:colon_idx].lower()
            value_str = token[colon_idx + 1 :]

            if key == "label":
                vals = _split_values(value_str)
                names = frozenset(_unquote(v) for v in vals if _is_complete_value(v))
                if names:
                    label_groups.append(names)
                continue

            if key == "assignee":
                vals = _split_values(value_str)
                logins = frozenset(_unquote(v).lower() for v in vals if _is_complete_value(v))
                if logins:
                    assignee_groups.append(logins)
                continue

            if key == "state":
                vals = _split_values(value_str)
                for v in vals:
                    if not _is_complete_value(v):
                        continue
                    v_lower = _unquote(v).lower()
                    if v_lower == "all":
                        state_parts.extend(["open", "closed"])
                    elif v_lower in _VALID_STATES:
                        state_parts.append(v_lower)
                    # else: unrecognized, contribute nothing (tolerant parse)
                continue

        # Bareword
        if token and not token.startswith(":"):
            barewords.append(token)

    state_set: frozenset[str] | None = frozenset(state_parts) if state_parts else None
    return ParsedQuery(
        state_set=state_set,
        label_groups=tuple(label_groups),
        assignee_groups=tuple(assignee_groups),
        barewords=tuple(barewords),
        quoted_barewords=tuple(quoted_barewords),
    )


def apply_parsed(
    items: list[Item],
    parsed: ParsedQuery,
    *,
    current_user_login: str | None,
) -> list[Item]:
    """Apply a ParsedQuery to a list of Items. Returns matching items in input order."""
    result = items

    if parsed.state_set is not None:
        result = [i for i in result if i.state in parsed.state_set]

    for or_group in parsed.label_groups:
        lower_group = {v.lower() for v in or_group}
        result = [i for i in result if any(lbl.lower() in lower_group for lbl in i.labels)]

    for or_group in parsed.assignee_groups:
        resolved = _resolve_assignee_group(or_group, current_user_login)
        result = [i for i in result if _matches_assignee(i, resolved)]

    for word in parsed.barewords:
        result = _filter_by_bareword(result, word)

    for word in parsed.quoted_barewords:
        lower = word.lower()
        result = [i for i in result if lower in i.title.lower()]

    return result


def _resolve_assignee_group(
    group: frozenset[str], current_user_login: str | None
) -> frozenset[str]:
    """Substitute 'me' with the resolved login if available."""
    if "me" in group and current_user_login:
        return (group - {"me"}) | {current_user_login.lower()}
    return group


def _matches_assignee(item: Item, group: frozenset[str]) -> bool:
    if "unassigned" in group:
        return not item.assignees
    return any(a.lower() in group for a in item.assignees)


def _filter_by_bareword(items: list[Item], word: str) -> list[Item]:
    stripped = word.lstrip("#")
    if stripped.isdigit():
        num = stripped
        lower_word = stripped.lower()
        return [i for i in items if i.item_id == num or lower_word in i.title.lower()]
    lower = word.lower()
    return [i for i in items if lower in i.title.lower()]


class FilterSuggester(Suggester):
    """Textual Suggester for the typed-query filter input.

    Completes keys (label:, assignee:, state:) and their values, including
    comma-list continuation and repeated-key exclusion.
    """

    def __init__(
        self,
        *,
        get_labels: Callable[[], list[str]],
        get_assignees: Callable[[], list[str]],
        get_current_user_login: Callable[[], str | None],
    ) -> None:
        super().__init__(use_cache=False, case_sensitive=True)
        self._get_labels = get_labels
        self._get_assignees = get_assignees
        self._get_login = get_current_user_login

    async def get_suggestion(self, value: str) -> str | None:
        if value.endswith(" "):
            return None

        # Split off the last whitespace-separated segment
        last_space = _last_unquoted(value, " ")
        if last_space == -1:
            prefix = ""
            current = value
        else:
            prefix = value[: last_space + 1]
            current = value[last_space + 1 :]

        if not current:
            return None

        completed = self._complete_token(current, prefix)
        if completed is None:
            return None
        return prefix + completed

    def _complete_token(self, current: str, prefix: str) -> str | None:
        colon_idx = current.find(":")
        if colon_idx > 0:
            key = current[:colon_idx].lower()
            value_part = current[colon_idx + 1 :]
            if key == "label":
                return self._complete_value(current, key, value_part, prefix, "label")
            if key == "assignee":
                return self._complete_value(current, key, value_part, prefix, "assignee")
            if key == "state":
                return self._complete_state(key, value_part)
            return None

        # Key prefix completion
        lower = current.lower()
        for key_name in ("label", "assignee", "state"):
            if key_name.startswith(lower) and lower:
                return f"{key_name}:"
        return None

    def _complete_value(
        self, full: str, key: str, value_part: str, prefix: str, kind: str
    ) -> str | None:
        existing = self._existing_key_values(prefix, kind)
        last_comma = _last_unquoted(value_part, ",")
        if last_comma >= 0:
            before_comma = value_part[: last_comma + 1]
            partial = value_part[last_comma + 1 :]
            current_vals = {_unquote(v).lower() for v in _split_values(before_comma) if v}
            exclude = existing | current_vals
        else:
            before_comma = ""
            partial = value_part
            exclude = existing

        candidates = self._candidates(kind)
        candidates = [c for c in candidates if c.lower() not in exclude]

        partial_lower = partial.lower()
        match = next((c for c in candidates if c.lower().startswith(partial_lower)), None)
        if match is None:
            return None
        matched_val = f'"{match}"' if " " in match else match
        return f"{key}:{before_comma}{matched_val}"

    def _complete_state(self, key: str, value_part: str) -> str | None:
        _STATE_CANDIDATES = ["open", "closed", "all"]
        last_comma = _last_unquoted(value_part, ",")
        if last_comma >= 0:
            before_comma = value_part[: last_comma + 1]
            partial = value_part[last_comma + 1 :]
        else:
            before_comma = ""
            partial = value_part
        partial_lower = partial.lower()
        match = next(
            (s for s in _STATE_CANDIDATES if s.startswith(partial_lower) and s != partial_lower),
            None,
        )
        if match is None:
            return None
        return f"{key}:{before_comma}{match}"

    def _candidates(self, kind: str) -> list[str]:
        if kind == "label":
            return sorted(self._get_labels())
        # assignee: me and unassigned first (if login known), then sorted logins
        login = self._get_login()
        result: list[str] = []
        if login:
            result.append("me")
        result.append("unassigned")
        result.extend(sorted(self._get_assignees()))
        # Deduplicate preserving order
        seen: set[str] = set()
        unique: list[str] = []
        for c in result:
            if c.lower() not in seen:
                seen.add(c.lower())
                unique.append(c)
        return unique

    def _existing_key_values(self, prefix: str, kind: str) -> frozenset[str]:
        """Collect values already used for this key in the tokens before current."""
        tokens = _tokenize(prefix.strip())
        result: set[str] = set()
        for token in tokens:
            colon_idx = token.find(":")
            if colon_idx > 0 and token[:colon_idx].lower() == kind:
                for v in _split_values(token[colon_idx + 1 :]):
                    result.add(_unquote(v).lower())
        return frozenset(result)
