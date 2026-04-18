"""Tests for RalphWorkflow._slugify helper."""

from cog.workflows.ralph import _slugify


def test_slug_lowercase() -> None:
    assert _slugify("Hello World") == "hello-world"


def test_slug_replaces_special_chars() -> None:
    # ": " is one run of non-alphanumeric chars → single dash; "!" at end → stripped
    assert _slugify("Fix: the bug!") == "fix-the-bug"


def test_slug_collapses_repeated_dashes() -> None:
    assert _slugify("foo---bar") == "foo-bar"


def test_slug_strips_leading_trailing_dashes() -> None:
    assert _slugify("  hello  ") == "hello"


def test_slug_caps_at_50_chars() -> None:
    long_title = "a" * 60
    result = _slugify(long_title)
    assert len(result) <= 50


def test_slug_caps_preserves_no_trailing_dash() -> None:
    # Construct a title that would produce a dash right at position 50
    title = "a" * 49 + " extra words here"
    result = _slugify(title)
    assert not result.endswith("-")
    assert len(result) <= 50


def test_slug_all_special_fallback_to_issue() -> None:
    assert _slugify("!!!") == "issue"


def test_slug_empty_fallback_to_issue() -> None:
    assert _slugify("") == "issue"
