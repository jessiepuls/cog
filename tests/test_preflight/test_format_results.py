from io import StringIO

from rich.console import Console

from cog.core.preflight import PreflightResult, format_result, print_results


def _result(ok: bool, level: str = "error", message: str = "msg") -> PreflightResult:
    from typing import Literal

    lv: Literal["error", "warning"] = "error" if level == "error" else "warning"
    return PreflightResult(check="test", ok=ok, level=lv, message=message)


def _capture(results: list[PreflightResult]) -> str:
    buf = StringIO()
    console = Console(file=buf, no_color=True, highlight=False)
    print_results(results, _console=console)
    return buf.getvalue()


def test_passing_line_prefix_check_mark() -> None:
    assert format_result(_result(ok=True)).startswith("✓ ")


def test_error_line_prefix() -> None:
    assert format_result(_result(ok=False, level="error")).startswith("✗ ")


def test_warning_line_prefix() -> None:
    assert format_result(_result(ok=False, level="warning")).startswith("⚠ ")


def test_summary_line_counts() -> None:
    results = [
        _result(ok=False, level="error"),
        _result(ok=False, level="error"),
        _result(ok=False, level="warning"),
    ]
    output = _capture(results)
    assert "2 errors" in output
    assert "1 warning" in output


def test_all_pass_summary() -> None:
    results = [_result(ok=True), _result(ok=True)]
    output = _capture(results)
    assert "all checks passed" in output
