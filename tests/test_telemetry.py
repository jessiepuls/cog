"""Tests for TelemetryWriter behavior."""

import fcntl
import json
from unittest.mock import patch

from cog.telemetry import TelemetryOutcome, TelemetryRecord, TelemetryWriter


def _make_record(outcome: TelemetryOutcome = "success") -> TelemetryRecord:
    return TelemetryRecord(
        ts="2024-01-01T00:00:00+00:00",
        cog_version="0.1.0",
        project="test-proj",
        workflow="ralph",
        item=42,
        outcome=outcome,
        branch="some-branch",
        pr_url=None,
        duration_seconds=1.5,
        stages=(),
        total_cost_usd=0.0,
        error=None,
    )


async def test_writer_creates_file_on_first_write(tmp_path):
    state_dir = tmp_path / "state"
    writer = TelemetryWriter(state_dir)
    assert not (state_dir / "runs.jsonl").exists()

    await writer.write(_make_record())

    assert (state_dir / "runs.jsonl").exists()


async def test_writer_appends_as_separate_lines(tmp_path):
    state_dir = tmp_path / "state"
    writer = TelemetryWriter(state_dir)

    await writer.write(_make_record("success"))
    await writer.write(_make_record("no-op"))

    lines = (state_dir / "runs.jsonl").read_text().splitlines()
    assert len(lines) == 2


async def test_writer_json_shape_round_trips(tmp_path):
    state_dir = tmp_path / "state"
    writer = TelemetryWriter(state_dir)
    record = _make_record()

    await writer.write(record)

    line = (state_dir / "runs.jsonl").read_text().strip()
    data = json.loads(line)
    assert data["ts"] == record.ts
    assert data["project"] == "test-proj"
    assert data["workflow"] == "ralph"
    assert data["item"] == 42
    assert data["outcome"] == "success"
    assert data["branch"] == "some-branch"
    assert data["pr_url"] is None
    assert data["duration_seconds"] == 1.5
    assert data["stages"] == []
    assert data["total_cost_usd"] == 0.0
    assert data["error"] is None


async def test_writer_disk_error_warns_no_raise(tmp_path, capsys):
    state_dir = tmp_path / "state"
    writer = TelemetryWriter(state_dir)

    with patch.object(writer, "_append", side_effect=OSError("disk full")):
        # Should not raise
        await writer.write(_make_record())

    captured = capsys.readouterr()
    assert "telemetry write failed" in captured.err


async def test_writer_acquires_flock(tmp_path):
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    writer = TelemetryWriter(state_dir)
    # Pre-create the file so _append opens it
    (state_dir / "runs.jsonl").touch()

    with patch("fcntl.flock") as mock_flock:
        writer._append('{"x": 1}\n')

    mock_flock.assert_called_once()
    args = mock_flock.call_args[0]
    assert args[1] == fcntl.LOCK_EX


async def test_writer_fsyncs(tmp_path):
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    writer = TelemetryWriter(state_dir)
    (state_dir / "runs.jsonl").touch()

    with patch("os.fsync") as mock_fsync:
        writer._append('{"x": 1}\n')

    mock_fsync.assert_called_once()
