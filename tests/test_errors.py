"""Tests for StageError message formatting."""

from pathlib import Path

from cog.core.errors import StageError
from cog.core.outcomes import StageResult
from cog.core.stage import Stage
from tests.fakes import EchoRunner


def _make_stage(name: str = "build") -> Stage:
    return Stage(name=name, prompt_source=lambda _: "", model="m", runner=EchoRunner())


def _make_result(stage: Stage, *, exit_status: int = 0) -> StageResult:
    return StageResult(
        stage=stage,
        duration_seconds=1.0,
        cost_usd=0.0,
        exit_status=exit_status,
        final_message="",
        stream_json_path=Path("/dev/null"),
        commits_created=0,
    )


def test_stage_error_no_cause_no_result_matches_legacy_format():
    err = StageError(_make_stage())
    assert str(err) == "stage 'build' failed"


def test_stage_error_with_cause_includes_cause_class_and_message():
    stage = _make_stage()
    cause = RuntimeError("runner exploded")
    err = StageError(stage, cause=cause)
    assert "cause=RuntimeError: runner exploded" in str(err)


def test_stage_error_with_result_non_zero_exit_includes_exit_status():
    stage = _make_stage()
    result = _make_result(stage, exit_status=-1)
    err = StageError(stage, result=result)
    assert "exit_status=-1" in str(err)


def test_stage_error_with_both_cause_and_exit_status():
    stage = _make_stage()
    cause = RuntimeError("exploded")
    result = _make_result(stage, exit_status=-1)
    err = StageError(stage, result=result, cause=cause)
    s = str(err)
    assert s.startswith("stage 'build' failed")
    assert "cause=RuntimeError: exploded" in s
    assert "exit_status=-1" in s
    assert s.index("cause=") < s.index("exit_status=")


def test_stage_error_result_with_zero_exit_does_not_add_exit_status():
    stage = _make_stage()
    result = _make_result(stage, exit_status=0)
    err = StageError(stage, result=result)
    assert "exit_status" not in str(err)


def test_stage_error_cause_preserves_type():
    class CustomError(Exception):
        pass

    stage = _make_stage()
    cause = CustomError("something")
    err = StageError(stage, cause=cause)
    assert err.cause is cause
    assert isinstance(err.cause, CustomError)


def test_stage_error_stage_name_in_message():
    err = StageError(_make_stage("review"))
    assert "review" in str(err)
