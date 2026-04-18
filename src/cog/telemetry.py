"""Telemetry writer. Full implementation in #9."""

from pathlib import Path


class TelemetryWriter:
    """Records run telemetry to disk. Stub: full implementation in #9."""

    def __init__(self, state_dir: Path, *, project: str) -> None:
        self._state_dir = state_dir
        self._project = project
