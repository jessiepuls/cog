"""Tests for RalphWorkflow stage definitions and model env-var overrides."""

from cog.workflows.ralph import RalphWorkflow
from tests.fakes import EchoRunner, InMemoryStateCache, make_item


def _make_ctx(tmp_path, item=None):
    from cog.core.context import ExecutionContext

    return ExecutionContext(
        project_dir=tmp_path,
        tmp_dir=tmp_path,
        state_cache=InMemoryStateCache(),
        headless=True,
        item=item or make_item(),
    )


def test_stages_returns_three_in_order(tmp_path):
    wf = RalphWorkflow(EchoRunner())
    stages = wf.stages(_make_ctx(tmp_path))
    assert [s.name for s in stages] == ["build", "review", "document"]


def test_all_stages_use_provided_runner(tmp_path):
    runner = EchoRunner()
    wf = RalphWorkflow(runner)
    stages = wf.stages(_make_ctx(tmp_path))
    assert all(s.runner is runner for s in stages)


def test_document_stage_tolerate_failure_true(tmp_path):
    wf = RalphWorkflow(EchoRunner())
    stages = wf.stages(_make_ctx(tmp_path))
    doc = next(s for s in stages if s.name == "document")
    assert doc.tolerate_failure is True


def test_build_and_review_tolerate_failure_false(tmp_path):
    wf = RalphWorkflow(EchoRunner())
    stages = wf.stages(_make_ctx(tmp_path))
    for stage in stages:
        if stage.name in ("build", "review"):
            assert stage.tolerate_failure is False


def test_default_models_are_claude_defaults(tmp_path, monkeypatch):
    for var in ("COG_RALPH_BUILD_MODEL", "COG_RALPH_REVIEW_MODEL", "COG_RALPH_DOCUMENT_MODEL"):
        monkeypatch.delenv(var, raising=False)
    wf = RalphWorkflow(EchoRunner())
    stages = {s.name: s for s in wf.stages(_make_ctx(tmp_path))}
    assert "sonnet-4-6" in stages["build"].model
    assert "opus-4-6" in stages["review"].model
    assert "sonnet-4-6" in stages["document"].model


def test_env_var_overrides_build_model(tmp_path, monkeypatch):
    monkeypatch.setenv("COG_RALPH_BUILD_MODEL", "my-custom-model")
    wf = RalphWorkflow(EchoRunner())
    stages = {s.name: s for s in wf.stages(_make_ctx(tmp_path))}
    assert stages["build"].model == "my-custom-model"


def test_env_var_overrides_review_model(tmp_path, monkeypatch):
    monkeypatch.setenv("COG_RALPH_REVIEW_MODEL", "review-model-x")
    wf = RalphWorkflow(EchoRunner())
    stages = {s.name: s for s in wf.stages(_make_ctx(tmp_path))}
    assert stages["review"].model == "review-model-x"


def test_env_var_overrides_document_model(tmp_path, monkeypatch):
    monkeypatch.setenv("COG_RALPH_DOCUMENT_MODEL", "doc-model-z")
    wf = RalphWorkflow(EchoRunner())
    stages = {s.name: s for s in wf.stages(_make_ctx(tmp_path))}
    assert stages["document"].model == "doc-model-z"


def test_env_var_read_at_stages_call_time(tmp_path, monkeypatch):
    monkeypatch.delenv("COG_RALPH_BUILD_MODEL", raising=False)
    wf = RalphWorkflow(EchoRunner())
    ctx = _make_ctx(tmp_path)
    first = {s.name: s for s in wf.stages(ctx)}
    monkeypatch.setenv("COG_RALPH_BUILD_MODEL", "new-model")
    second = {s.name: s for s in wf.stages(ctx)}
    assert first["build"].model != "new-model"
    assert second["build"].model == "new-model"
