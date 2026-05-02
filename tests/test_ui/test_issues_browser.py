"""Widget-level tests for issues_browser.py (#189)."""

from __future__ import annotations

from datetime import UTC, datetime

from cog.core.item import Item
from cog.ui.widgets.issues_browser import _row_text
from tests.fakes import make_item

_BASE_DT = datetime(2026, 1, 1, tzinfo=UTC)


def _item(
    item_id: str = "1",
    title: str = "title",
    labels: tuple[str, ...] = (),
    state: str = "open",
) -> Item:
    return make_item(item_id=item_id, title=title, labels=labels, state=state)


# --- Row text rendering ---


def test_row_text_number_prefix() -> None:
    row = _row_text(_item("189", title="Test"))
    assert "#189" in row


def test_row_text_title_present() -> None:
    row = _row_text(_item("1", title="Fix login bug"))
    assert "Fix login bug" in row


def test_row_text_title_truncated_when_long() -> None:
    long_title = "A" * 200
    row = _row_text(_item("1", title=long_title), width=80)
    assert "…" in row
    assert len(row) < 250  # reasonable bound


def test_row_text_label_chips_shown() -> None:
    row = _row_text(_item("1", labels=("bug",)))
    assert "bug" in row


def test_row_text_agent_failed_glyph() -> None:
    row = _row_text(_item("1", labels=("agent-failed",)))
    assert "⚠" in row


def test_row_text_no_glyph_without_agent_failed() -> None:
    row = _row_text(_item("1", labels=("bug",)))
    assert "⚠" not in row


def test_row_text_closed_has_dim_and_strikethrough() -> None:
    row = _row_text(_item("1", title="Closed issue", state="closed"))
    assert "dim" in row or "strike" in row


def test_row_text_open_no_dim() -> None:
    row = _row_text(_item("1", title="Open issue", state="open"))
    # Should not wrap entire row in [dim]
    assert not row.startswith("[dim]")
