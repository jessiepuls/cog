from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from textual.app import ComposeResult
from textual.widget import Widget

from cog.core.item import Comment, Item
from cog.core.outcomes import StageResult
from cog.core.runner import AgentRunner, ResultEvent, RunEvent, RunResult
from cog.core.stage import Stage

_EPOCH = datetime(2024, 1, 1, tzinfo=UTC)

_DUMMY_STAGE = Stage(
    name="build",
    prompt_source=lambda _: "hello",
    model="claude-sonnet-4-6",
    runner=None,  # type: ignore[arg-type]
)


def make_stage_result(
    stage_name: str = "build",
    *,
    cost: float = 0.0,
    commits: int = 0,
    final_message: str = "",
    error: Exception | None = None,
    duration: float = 0.0,
) -> StageResult:
    stage = Stage(
        name=stage_name,
        prompt_source=lambda _: "hello",
        model="claude-sonnet-4-6",
        runner=None,  # type: ignore[arg-type]
    )
    return StageResult(
        stage=stage,
        duration_seconds=duration,
        cost_usd=cost,
        exit_status=0,
        final_message=final_message,
        stream_json_path=Path("/dev/null"),
        commits_created=commits,
        error=error,
    )


def make_item(
    *,
    tracker_id: str = "github/org/repo",
    item_id: str = "1",
    title: str = "Test item",
    body: str = "",
    labels: tuple[str, ...] = (),
    comments: tuple[Comment, ...] = (),
    state: str = "open",
    created_at: datetime | None = None,
    updated_at: datetime | None = None,
    url: str = "https://github.com/org/repo/issues/1",
) -> Item:
    return Item(
        tracker_id=tracker_id,
        item_id=item_id,
        title=title,
        body=body,
        labels=labels,
        comments=comments,
        state=state,
        created_at=created_at or _EPOCH,
        updated_at=updated_at or _EPOCH,
        url=url,
    )


def make_item_with_blocker_refs(refs: list[int], *, item_id: str = "1") -> Item:
    """Construct an Item whose body contains 'blocked by #N' for each ref."""
    body = " ".join(f"blocked by #{n}" for n in refs)
    return make_item(item_id=item_id, body=body)


@dataclass
class RecordingEventSink:
    """Captures emitted events for assertion in tests."""

    events: list[RunEvent] = field(default_factory=list)

    async def emit(self, event: RunEvent) -> None:
        self.events.append(event)


class EchoRunner(AgentRunner):
    """Returns the prompt as the final_message. Zero cost, zero duration."""

    async def stream(self, prompt: str, *, model: str) -> AsyncIterator[RunEvent]:
        yield ResultEvent(
            result=RunResult(
                final_message=prompt,
                total_cost_usd=0.0,
                exit_status=0,
                stream_json_path=Path("/dev/null"),
                duration_seconds=0.0,
            )
        )


class NullContentWidget(Widget):
    """Minimal Widget implementing emit; used in wire/run-screen smoke tests."""

    def compose(self) -> ComposeResult:
        return iter([])

    async def emit(self, event: RunEvent) -> None:
        pass


class InMemoryStateCache:
    """Dict-backed StateCache. Structurally satisfies the StateCache Protocol."""

    def __init__(self) -> None:
        self._processed: dict[str, str] = {}
        self._deferred: dict[str, dict[str, object]] = {}

    def _key(self, item: Item) -> str:
        return f"{item.tracker_id}:{item.item_id}"

    def is_processed(self, item: Item) -> bool:
        return self._key(item) in self._processed

    def mark_processed(self, item: Item, outcome: str) -> None:
        self._processed[self._key(item)] = outcome

    def is_deferred(self, item: Item) -> bool:
        return self._key(item) in self._deferred

    def mark_deferred(self, item: Item, reason: str, blockers: list[str]) -> None:
        self._deferred[self._key(item)] = {"reason": reason, "blockers": blockers}

    def clear_deferral(self, item: Item) -> None:
        self._deferred.pop(self._key(item), None)

    def save(self) -> None:
        pass


@dataclass
class FakeProc:
    stdout: bytes
    stderr: bytes = b""
    returncode: int = 0
    received_stdin: bytes | None = None

    async def communicate(self, input: bytes | None = None) -> tuple[bytes, bytes]:
        self.received_stdin = input
        return self.stdout, self.stderr


class FailingRunner(AgentRunner):
    """Raises exc from stream(), simulating a runner crash."""

    def __init__(self, exc: Exception | None = None) -> None:
        self._exc = exc or RuntimeError("runner failed")

    async def stream(self, prompt: str, *, model: str) -> AsyncIterator[RunEvent]:
        raise self._exc
        yield  # type: ignore[misc]


class ExitNonZeroRunner(AgentRunner):
    """Returns a RunResult with the given non-zero exit status."""

    def __init__(self, exit_status: int = 1) -> None:
        self._exit_status = exit_status

    async def stream(self, prompt: str, *, model: str) -> AsyncIterator[RunEvent]:
        yield ResultEvent(
            result=RunResult(
                final_message="non-zero exit",
                total_cost_usd=0.0,
                exit_status=self._exit_status,
                stream_json_path=Path("/dev/null"),
                duration_seconds=0.0,
            )
        )


class FakeSubprocessRegistry:
    """Maps argv tuples to FakeProc results.

    Tests register expected invocations; an unexpected argv raises a clear error.
    """

    def __init__(self) -> None:
        self._expectations: dict[tuple[str, ...], FakeProc] = {}
        self._calls: list[tuple[str, ...]] = []
        self._procs: list[FakeProc] = []

    def expect(
        self,
        argv: Sequence[str],
        *,
        stdout: bytes = b"",
        stderr: bytes = b"",
        returncode: int = 0,
    ) -> FakeProc:
        """Register an expectation. Returns the FakeProc so tests can inspect
        `received_stdin` after the call runs."""
        proc = FakeProc(stdout=stdout, stderr=stderr, returncode=returncode)
        self._expectations[tuple(argv)] = proc
        return proc

    @property
    def calls(self) -> list[tuple[str, ...]]:
        return list(self._calls)

    async def create_subprocess_exec(
        self,
        *argv: str,
        cwd: Any = None,
        stdin: Any = None,
        stdout: Any = None,
        stderr: Any = None,
    ) -> FakeProc:
        key = tuple(argv)
        self._calls.append(key)
        if key not in self._expectations:
            raise AssertionError(f"Unexpected subprocess call: {key!r}")
        proc = self._expectations[key]
        self._procs.append(proc)
        return proc
