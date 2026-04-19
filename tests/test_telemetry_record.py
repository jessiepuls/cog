"""Tests for TelemetryRecord and StageTelemetry construction."""

import dataclasses
import json
from datetime import UTC, datetime
from pathlib import Path

from cog.core.item import Item
from cog.core.outcomes import StageResult
from cog.core.stage import Stage
from cog.telemetry import StageTelemetry, TelemetryRecord
from tests.fakes import EchoRunner


def _make_item(item_id: str = "42") -> Item:
    return Item(
        tracker_id="github/owner/repo",
        item_id=item_id,
        title="Test issue",
        body="",
        labels=(),
        comments=(),
        state="open",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        url="https://github.com/owner/repo/issues/42",
    )


def _make_stage(name: str = "build") -> Stage:
    return Stage(
        name=name,
        prompt_source=lambda _: "prompt",
        model="claude-opus-4-6",
        runner=EchoRunner(),
    )


def _make_stage_result(
    stage_name: str = "build",
    cost_usd: float = 0.01,
    exit_status: int = 0,
    commits: int = 1,
) -> StageResult:
    return StageResult(
        stage=_make_stage(stage_name),
        duration_seconds=10.0,
        cost_usd=cost_usd,
        exit_status=exit_status,
        final_message="done",
        stream_json_path=Path("/dev/null"),
        commits_created=commits,
    )


def test_build_populates_every_field():
    item = _make_item()
    results = [_make_stage_result()]

    record = TelemetryRecord.build(
        project="my-proj",
        workflow="ralph",
        item=item,
        outcome="success",
        results=results,
        branch="feat/branch",
        pr_url="https://github.com/owner/repo/pull/1",
        duration_seconds=15.0,
        error=None,
    )

    assert record.project == "my-proj"
    assert record.workflow == "ralph"
    assert record.item == 42
    assert record.outcome == "success"
    assert record.branch == "feat/branch"
    assert record.pr_url == "https://github.com/owner/repo/pull/1"
    assert record.duration_seconds == 15.0
    assert record.error is None
    assert len(record.stages) == 1
    assert record.cog_version != ""
    assert record.ts != ""


def test_cog_version_reads_from_static_package_attribute():
    # Regression guard: cog_version must come from cog.__version__ (a static
    # module attribute), not importlib.metadata.version(). The metadata path
    # raises PackageNotFoundError when cog's dist-info is missing or stale
    # (e.g., partially-built .venv), killing iterations mid-run.
    from cog import __version__ as expected

    record = TelemetryRecord.build(
        project="p",
        workflow="w",
        item=_make_item(),
        outcome="success",
        results=[],
        duration_seconds=1.0,
    )
    assert record.cog_version == expected


def test_cog_version_works_when_package_metadata_missing(monkeypatch):
    # Regression guard for PackageNotFoundError: simulate an environment where
    # importlib.metadata.version("cog") raises. The build() path must not
    # touch metadata and therefore must still succeed.
    import importlib.metadata

    def _raise(_name: str) -> str:
        raise importlib.metadata.PackageNotFoundError("cog")

    monkeypatch.setattr(importlib.metadata, "version", _raise)

    record = TelemetryRecord.build(
        project="p",
        workflow="w",
        item=_make_item(),
        outcome="success",
        results=[],
        duration_seconds=1.0,
    )
    assert record.cog_version != ""


def test_total_cost_sums_stages():
    results = [
        _make_stage_result("s1", cost_usd=0.01),
        _make_stage_result("s2", cost_usd=0.02),
        _make_stage_result("s3", cost_usd=0.03),
    ]
    record = TelemetryRecord.build(
        project="p",
        workflow="w",
        item=_make_item(),
        outcome="success",
        results=results,
        duration_seconds=1.0,
    )
    assert abs(record.total_cost_usd - 0.06) < 1e-9


def test_empty_stages_zero_cost():
    record = TelemetryRecord.build(
        project="p",
        workflow="w",
        item=_make_item(),
        outcome="success",
        results=[],
        duration_seconds=0.5,
    )
    assert record.total_cost_usd == 0.0
    assert record.stages == ()


def test_item_id_coerced_to_int():
    item = _make_item(item_id="42")
    record = TelemetryRecord.build(
        project="p",
        workflow="w",
        item=item,
        outcome="success",
        results=[],
        duration_seconds=1.0,
    )
    assert record.item == 42
    assert isinstance(record.item, int)


def test_timestamp_is_utc_aware_iso():
    record = TelemetryRecord.build(
        project="p",
        workflow="w",
        item=_make_item(),
        outcome="success",
        results=[],
        duration_seconds=1.0,
    )
    dt = datetime.fromisoformat(record.ts)
    assert dt.tzinfo is not None
    assert dt.tzinfo.utcoffset(dt).total_seconds() == 0  # type: ignore[union-attr]


def test_stage_telemetry_from_result_mapping():
    result = _make_stage_result("build", cost_usd=0.05, exit_status=0, commits=2)
    st = StageTelemetry.from_stage_result(result)

    assert st.stage == "build"
    assert st.model == "claude-opus-4-6"
    assert st.duration_s == 10.0
    assert st.cost_usd == 0.05
    assert st.exit_status == 0
    assert st.commits == 2
    assert st.input_tokens == 0
    assert st.output_tokens == 0


def test_outcome_literal_accepted():
    # All valid TelemetryOutcome values are accepted at runtime
    for outcome in ("success", "no-op", "error", "push-failed", "deferred-by-blocker"):
        record = TelemetryRecord.build(
            project="p",
            workflow="w",
            item=_make_item(),
            outcome=outcome,  # type: ignore[arg-type]
            results=[],
            duration_seconds=1.0,
        )
        assert record.outcome == outcome


def test_telemetry_record_cause_class_default_is_none():
    record = TelemetryRecord.build(
        project="p",
        workflow="w",
        item=_make_item(),
        outcome="success",
        results=[],
        duration_seconds=1.0,
    )
    assert record.cause_class is None


def test_telemetry_record_cause_class_populated_when_provided():
    record = TelemetryRecord.build(
        project="p",
        workflow="w",
        item=_make_item(),
        outcome="error",
        results=[],
        duration_seconds=1.0,
        cause_class="RunnerStalledError",
    )
    assert record.cause_class == "RunnerStalledError"


def test_telemetry_record_cause_class_none_for_generic_exception():
    record = TelemetryRecord.build(
        project="p",
        workflow="w",
        item=_make_item(),
        outcome="error",
        results=[],
        duration_seconds=1.0,
        error="something went wrong",
        cause_class=None,
    )
    assert record.cause_class is None


def test_telemetry_record_json_includes_cause_class():
    record = TelemetryRecord.build(
        project="p",
        workflow="w",
        item=_make_item(),
        outcome="error",
        results=[],
        duration_seconds=1.0,
        error="some error",
        cause_class="RunnerTimeoutError",
    )
    d = dataclasses.asdict(record)
    line = json.dumps(d)
    assert "cause_class" in line
    assert "RunnerTimeoutError" in line


def test_build_accepts_extra_stages_kwarg():
    item = _make_item()
    interview_stage = StageTelemetry(
        stage="interview",
        model="claude-sonnet-4-6",
        duration_s=5.0,
        cost_usd=0.10,
        exit_status=0,
        commits=0,
    )
    record = TelemetryRecord.build(
        project="p",
        workflow="refine",
        item=item,
        outcome="error",
        results=[],
        extra_stages=(interview_stage,),
        duration_seconds=5.0,
    )
    assert len(record.stages) == 1
    assert record.stages[0].stage == "interview"
    assert abs(record.total_cost_usd - 0.10) < 1e-9


def test_build_extra_stages_prepended_before_result_stages():
    item = _make_item()
    interview_stage = StageTelemetry(
        stage="interview",
        model="claude-sonnet-4-6",
        duration_s=2.0,
        cost_usd=0.05,
        exit_status=0,
        commits=0,
    )
    result = _make_stage_result("build", cost_usd=0.10)
    record = TelemetryRecord.build(
        project="p",
        workflow="refine",
        item=item,
        outcome="success",
        results=[result],
        extra_stages=(interview_stage,),
        duration_seconds=10.0,
    )
    assert record.stages[0].stage == "interview"
    assert record.stages[1].stage == "build"
    assert abs(record.total_cost_usd - 0.15) < 1e-9


def test_telemetry_record_backward_compat_missing_cause_class():
    # Old JSONL lines without cause_class should parse correctly
    # when cause_class defaults to None
    record = TelemetryRecord.build(
        project="p",
        workflow="w",
        item=_make_item(),
        outcome="error",
        results=[],
        duration_seconds=1.0,
        error="some error",
    )
    assert record.cause_class is None
    d = dataclasses.asdict(record)
    # Simulate old JSONL that doesn't have cause_class
    d.pop("cause_class", None)
    # Re-constructing from dict without cause_class uses the default
    reconstructed = TelemetryRecord(**{**d, "stages": tuple(d["stages"])})  # type: ignore[arg-type]
    assert reconstructed.cause_class is None
