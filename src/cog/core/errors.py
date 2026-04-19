from cog.core.outcomes import StageResult
from cog.core.stage import Stage


class WorkflowError(Exception):
    pass


class StageError(WorkflowError):
    def __init__(
        self,
        stage: Stage,
        result: StageResult | None = None,
        cause: Exception | None = None,
    ) -> None:
        self.stage = stage
        self.result = result
        self.cause = cause
        parts = [f"stage {stage.name!r} failed"]
        if cause is not None:
            parts.append(f"cause={type(cause).__name__}: {cause}")
        if result is not None and result.exit_status not in (0, None):
            parts.append(f"exit_status={result.exit_status}")
        super().__init__(" | ".join(parts))


class RunnerError(Exception):
    """Base for runner failures."""


class RunnerTimeoutError(RunnerError):
    """Subprocess exceeded COG_RUNNER_TIMEOUT_SECONDS; process was terminated."""


class RunnerStalledError(RunnerError):
    """Subprocess produced no stream events within the inactivity window; terminated."""

    def __init__(
        self,
        *,
        inactivity_seconds: float,
        last_event_summary: str | None = None,
    ) -> None:
        self.inactivity_seconds = inactivity_seconds
        self.last_event_summary = last_event_summary
        super().__init__(
            f"no stream event for {inactivity_seconds:.0f}s; subprocess terminated. "
            f"last event: {last_event_summary or '(none)'}"
        )


class StreamJsonParseError(RunnerError):
    """Claude emitted a line that wasn't parseable JSON or had unexpected shape."""


class HostError(Exception):
    """Non-zero exit or parse failure from a git host."""


class TrackerError(Exception):
    """Non-zero exit or parse failure from an issue tracker."""


class SandboxError(Exception):
    """Base for sandbox failures."""


class DockerUnavailableError(SandboxError):
    """docker daemon unreachable (`docker info` failed or binary missing)."""


class DockerImageBuildError(SandboxError):
    """`docker build` exited non-zero."""


class GitError(Exception):
    """Non-zero exit or unexpected output from a git subprocess."""


class RebaseUnresolvedError(Exception):
    """Raised (conceptually) when claude's rebase stage exits mid-state."""

    def __init__(self, final_message: str) -> None:
        self.final_message = final_message
        super().__init__(
            f"Rebase could not be completed by claude. Analysis: {final_message[:200]}"
        )


class CiError(Exception):
    """Base for CI-related failures."""


class CiChecksFailedError(CiError):
    def __init__(self, failing: tuple[str, ...]) -> None:
        self.failing = failing
        super().__init__(f"CI checks failed: {', '.join(failing)}")


class CiTimeoutError(CiError):
    def __init__(self, timeout_seconds: float) -> None:
        self.timeout_seconds = timeout_seconds
        super().__init__(f"CI did not resolve within {timeout_seconds:.0f}s")
