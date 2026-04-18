import asyncio
from pathlib import Path

from cog.core.preflight import PreflightResult, run_checks


class _PassCheck:
    name = "pass"
    level = "error"

    async def run(self, project_dir: Path) -> PreflightResult:
        return PreflightResult(check=self.name, ok=True, level="error", message="ok")


class _FailErrorCheck:
    name = "fail_error"
    level = "error"

    async def run(self, project_dir: Path) -> PreflightResult:
        return PreflightResult(check=self.name, ok=False, level="error", message="error")


class _FailWarnCheck:
    name = "fail_warn"
    level = "warning"

    async def run(self, project_dir: Path) -> PreflightResult:
        return PreflightResult(check=self.name, ok=False, level="warning", message="warn")


class _SlowCheck:
    def __init__(self, delay: float, name: str, ok: bool) -> None:
        self.delay = delay
        self.name = name
        self.level = "error"
        self._ok = ok

    async def run(self, project_dir: Path) -> PreflightResult:
        await asyncio.sleep(self.delay)
        return PreflightResult(check=self.name, ok=self._ok, level="error", message="")


async def test_collects_all_results_no_short_circuit(tmp_path: Path) -> None:
    checks = [_FailErrorCheck(), _FailErrorCheck(), _PassCheck()]
    results = await run_checks(checks, tmp_path)
    assert len(results) == 3
    assert results[0].ok is False
    assert results[1].ok is False
    assert results[2].ok is True


async def test_concurrent_execution(tmp_path: Path) -> None:
    # Slow check first, fast check second — result order must still match input order.
    checks = [_SlowCheck(0.05, "slow", ok=False), _SlowCheck(0.01, "fast", ok=True)]
    results = await run_checks(checks, tmp_path)
    assert results[0].check == "slow"
    assert results[1].check == "fast"


async def test_ok_checks_report_ok_true(tmp_path: Path) -> None:
    results = await run_checks([_PassCheck()], tmp_path)
    assert results[0].ok is True


async def test_error_check_failure_reports_level_error(tmp_path: Path) -> None:
    results = await run_checks([_FailErrorCheck()], tmp_path)
    assert results[0].level == "error"
    assert results[0].ok is False


async def test_warning_check_failure_reports_level_warning(tmp_path: Path) -> None:
    results = await run_checks([_FailWarnCheck()], tmp_path)
    assert results[0].level == "warning"
    assert results[0].ok is False
