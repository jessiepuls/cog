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
        super().__init__(f"stage {stage.name!r} failed")


class RunnerError(Exception):
    """Base for runner failures."""


class RunnerTimeoutError(RunnerError):
    """Subprocess exceeded COG_RUNNER_TIMEOUT_SECONDS; process was terminated."""


class StreamJsonParseError(RunnerError):
    """Claude emitted a line that wasn't parseable JSON or had unexpected shape."""


class TrackerError(Exception):
    """Non-zero exit or parse failure from an issue tracker."""


class SandboxError(Exception):
    """Base for sandbox failures."""


class DockerUnavailableError(SandboxError):
    """docker daemon unreachable (`docker info` failed or binary missing)."""


class DockerImageBuildError(SandboxError):
    """`docker build` exited non-zero."""
