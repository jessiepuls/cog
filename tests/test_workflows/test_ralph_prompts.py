"""Tests for RalphWorkflow prompt loading and assembly."""

from datetime import UTC, datetime

import pytest

from cog.core.context import ExecutionContext
from cog.core.item import Comment
from cog.workflows.ralph import _build_prompt, _load_prompt


def _make_ctx(tmp_path, item=None, work_branch=None):
    from tests.fakes import InMemoryStateCache

    return ExecutionContext(
        project_dir=tmp_path,
        tmp_dir=tmp_path,
        state_cache=InMemoryStateCache(),
        headless=True,
        item=item,
        work_branch=work_branch,
    )


def _make_item(**overrides):
    from tests.fakes import make_item

    return make_item(**overrides)


def test_load_prompt_reads_all_three_files():
    for stage in ("build", "review", "document"):
        text = _load_prompt(stage)
        assert len(text) > 0, f"{stage}.md is empty"


def test_build_prompt_contains_static_text(tmp_path):
    item = _make_item()
    ctx = _make_ctx(tmp_path, item=item)
    prompt = _build_prompt("build", ctx)
    assert "Ralph: build stage" in prompt


def test_build_prompt_includes_issue_number_and_title(tmp_path):
    item = _make_item(item_id="42", title="Test issue")
    ctx = _make_ctx(tmp_path, item=item)
    prompt = _build_prompt("build", ctx)
    assert "Issue #42: Test issue" in prompt


def test_build_prompt_includes_body(tmp_path):
    item = _make_item(body="The body content here.")
    ctx = _make_ctx(tmp_path, item=item)
    prompt = _build_prompt("build", ctx)
    assert "The body content here." in prompt


def test_build_prompt_includes_branch_when_set(tmp_path):
    item = _make_item()
    ctx = _make_ctx(tmp_path, item=item, work_branch="ralph/42-my-feature")
    prompt = _build_prompt("build", ctx)
    assert "Branch: ralph/42-my-feature" in prompt


def test_build_prompt_omits_branch_when_none(tmp_path):
    item = _make_item()
    ctx = _make_ctx(tmp_path, item=item, work_branch=None)
    prompt = _build_prompt("build", ctx)
    assert "Branch:" not in prompt


def test_build_prompt_includes_comments_section_when_present(tmp_path):
    comment = Comment(
        author="alice",
        body="Great idea!",
        created_at=datetime(2024, 3, 1, tzinfo=UTC),
    )
    item = _make_item(comments=(comment,))
    ctx = _make_ctx(tmp_path, item=item)
    prompt = _build_prompt("build", ctx)
    assert "### Comments" in prompt
    assert "alice" in prompt
    assert "Great idea!" in prompt


def test_build_prompt_omits_comments_section_when_empty(tmp_path):
    item = _make_item(comments=())
    ctx = _make_ctx(tmp_path, item=item)
    prompt = _build_prompt("build", ctx)
    assert "### Comments" not in prompt


def test_build_prompt_raises_when_item_unset(tmp_path):
    ctx = _make_ctx(tmp_path, item=None)
    with pytest.raises(AssertionError):
        _build_prompt("build", ctx)
