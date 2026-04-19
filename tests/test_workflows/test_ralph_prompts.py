"""Tests for RalphWorkflow prompt loading and assembly."""

import re
from datetime import UTC, datetime

import pytest

from cog.core.context import ExecutionContext
from cog.core.item import Comment
from cog.workflows.ralph import _build_prompt, _load_prompt
from tests.fakes import InMemoryStateCache, make_item

_UNBOUNDED_DIFF_RE = re.compile(r"git diff\s+(?:main|master)\.\.HEAD(?!\s+--?)")


def _make_ctx(tmp_path, item=None, work_branch=None):
    return ExecutionContext(
        project_dir=tmp_path,
        tmp_dir=tmp_path,
        state_cache=InMemoryStateCache(),
        headless=True,
        item=item,
        work_branch=work_branch,
    )


def test_load_prompt_reads_all_three_files():
    for stage in ("build", "review", "document"):
        text = _load_prompt(stage)
        assert len(text) > 0, f"{stage}.md is empty"


def test_build_prompt_contains_static_text(tmp_path):
    item = make_item()
    ctx = _make_ctx(tmp_path, item=item)
    prompt = _build_prompt("build", ctx)
    assert "Ralph: build stage" in prompt


def test_build_prompt_includes_issue_number_and_title(tmp_path):
    item = make_item(item_id="42", title="Test issue")
    ctx = _make_ctx(tmp_path, item=item)
    prompt = _build_prompt("build", ctx)
    assert "Issue #42: Test issue" in prompt


def test_build_prompt_includes_body(tmp_path):
    item = make_item(body="The body content here.")
    ctx = _make_ctx(tmp_path, item=item)
    prompt = _build_prompt("build", ctx)
    assert "The body content here." in prompt


def test_build_prompt_includes_branch_when_set(tmp_path):
    item = make_item()
    ctx = _make_ctx(tmp_path, item=item, work_branch="ralph/42-my-feature")
    prompt = _build_prompt("build", ctx)
    assert "Branch: ralph/42-my-feature" in prompt


def test_build_prompt_omits_branch_when_none(tmp_path):
    item = make_item()
    ctx = _make_ctx(tmp_path, item=item, work_branch=None)
    prompt = _build_prompt("build", ctx)
    assert "Branch:" not in prompt


def test_build_prompt_includes_comments_section_when_present(tmp_path):
    comment = Comment(
        author="alice",
        body="Great idea!",
        created_at=datetime(2024, 3, 1, tzinfo=UTC),
    )
    item = make_item(comments=(comment,))
    ctx = _make_ctx(tmp_path, item=item)
    prompt = _build_prompt("build", ctx)
    assert "### Comments" in prompt
    assert "alice" in prompt
    assert "Great idea!" in prompt


def test_build_prompt_omits_comments_section_when_empty(tmp_path):
    item = make_item(comments=())
    ctx = _make_ctx(tmp_path, item=item)
    prompt = _build_prompt("build", ctx)
    assert "### Comments" not in prompt


def test_build_prompt_raises_when_item_unset(tmp_path):
    ctx = _make_ctx(tmp_path, item=None)
    with pytest.raises(AssertionError):
        _build_prompt("build", ctx)


def test_no_unbounded_git_diff_in_prompts():
    for stage in ("build", "review", "document"):
        content = _load_prompt(stage)
        assert _UNBOUNDED_DIFF_RE.search(content) is None, (
            f"{stage}.md contains unbounded git diff — see #48/#49"
        )


def test_bounded_tool_calls_section_present():
    for stage in ("build", "review", "document"):
        content = _load_prompt(stage)
        assert "## Bounded tool calls (important)" in content, (
            f"{stage}.md is missing '## Bounded tool calls (important)' section"
        )


def test_tracker_agnostic_language():
    for stage in ("build", "review", "document"):
        content = _load_prompt(stage)
        assert "GitHub issue" not in content, (
            f"{stage}.md uses 'GitHub issue' — prefer 'tracked item'"
        )


def test_build_prompt_uses_tracked_item_language():
    content = _load_prompt("build")
    assert "tracked item" in content


def test_build_prompt_contains_summary_section_instruction():
    content = _load_prompt("build")
    assert "### Summary" in content


def test_build_prompt_contains_key_changes_section_instruction():
    content = _load_prompt("build")
    assert "### Key changes" in content


def test_build_prompt_keeps_test_plan_instruction():
    content = _load_prompt("build")
    assert "### Test plan" in content


def test_build_prompt_final_message_format_mentions_wrapper_extraction():
    content = _load_prompt("build")
    assert "wrapper extracts" in content


def _key_changes_section(content: str) -> str:
    match = re.search(
        r"### Key changes\n(.*?)(?=\n### Test plan\b)", content, re.DOTALL
    )
    assert match, "### Key changes section not found in build.md"
    return match.group(1)


def _test_plan_section(content: str) -> str:
    match = re.search(
        r"### Test plan\n(.*?)(?=\n## |\Z)", content, re.DOTALL
    )
    assert match, "### Test plan section not found in build.md"
    return match.group(1)


def test_build_prompt_key_changes_has_bullet_cap_guidance():
    content = _load_prompt("build")
    assert "3-7 bullets" in _key_changes_section(content)


def test_build_prompt_key_changes_mentions_conceptual_not_file_by_file():
    content = _load_prompt("build")
    key_changes = _key_changes_section(content)
    assert "conceptual" in key_changes
    assert "files you touched" not in content


def test_build_prompt_key_changes_example_has_three_to_seven_bullets():
    content = _load_prompt("build")
    key_changes = _key_changes_section(content)
    example_match = re.search(r"```\n### Key changes\n(.*?)```", key_changes, re.DOTALL)
    assert example_match, "No example block found in Key changes section"
    bullets = re.findall(r"^- ", example_match.group(1), re.MULTILINE)
    assert 3 <= len(bullets) <= 7, f"Example has {len(bullets)} bullets, expected 3-7"


def test_build_prompt_test_plan_has_perspective_framing():
    content = _load_prompt("build")
    assert "from the perspective" in _test_plan_section(content)


def test_build_prompt_test_plan_lists_change_type_categories():
    content = _load_prompt("build")
    test_plan = _test_plan_section(content)
    for category in (
        "UI / app code",
        "CLI / tooling",
        "Data migration / script",
        "API / library",
        "Config / infrastructure",
    ):
        assert category in test_plan, f"Missing category: {category}"


def test_build_prompt_test_plan_excludes_ci_items_block():
    content = _load_prompt("build")
    assert "Do not include tool-execution items that CI runs automatically" in _test_plan_section(content)


def test_build_prompt_test_plan_ci_exclusion_is_multi_ecosystem():
    content = _load_prompt("build")
    test_plan = _test_plan_section(content)
    for tool in ("pytest", "jest", "tsc", "eslint"):
        assert tool in test_plan, f"Missing multi-ecosystem CI tool example: {tool}"


def test_build_prompt_test_plan_defers_to_claude_md():
    content = _load_prompt("build")
    test_plan = _test_plan_section(content)
    assert "CLAUDE.md" in test_plan
