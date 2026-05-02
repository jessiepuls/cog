from __future__ import annotations

from collections.abc import AsyncIterator, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from textual.app import ComposeResult
from textual.widget import Widget

from cog.core.item import Comment, Item
from cog.core.outcomes import StageResult
from cog.core.runner import AgentRunner, ResultEvent, RunEvent, RunResult, ToolUseEvent
from cog.core.stage import Stage
from cog.core.tracker import IssueTracker, ItemListFilter, ItemListResult

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
    assignees: tuple[str, ...] = (),
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
        assignees=assignees,
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

    async def stream(
        self, prompt: str, *, model: str, cwd: Path | None = None
    ) -> AsyncIterator[RunEvent]:
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


class ScriptedFinalMessageRunner(AgentRunner):
    """Returns a preconfigured final_message. Used in integration tests."""

    def __init__(self, final_message: str, cost: float = 0.0) -> None:
        self._final_message = final_message
        self._cost = cost

    async def stream(
        self, prompt: str, *, model: str, cwd: Path | None = None
    ) -> AsyncIterator[RunEvent]:
        yield ResultEvent(
            result=RunResult(
                final_message=self._final_message,
                total_cost_usd=self._cost,
                exit_status=0,
                stream_json_path=Path("/dev/null"),
                duration_seconds=0.0,
            )
        )


class FailingRunner(AgentRunner):
    """Raises exc from stream(), simulating a runner crash."""

    def __init__(self, exc: Exception | None = None) -> None:
        self._exc = exc or RuntimeError("runner failed")

    async def stream(
        self, prompt: str, *, model: str, cwd: Path | None = None
    ) -> AsyncIterator[RunEvent]:
        raise self._exc
        yield  # type: ignore[misc]


class ExitNonZeroRunner(AgentRunner):
    """Returns a RunResult with the given non-zero exit status."""

    def __init__(self, exit_status: int = 1) -> None:
        self._exit_status = exit_status

    async def stream(
        self, prompt: str, *, model: str, cwd: Path | None = None
    ) -> AsyncIterator[RunEvent]:
        yield ResultEvent(
            result=RunResult(
                final_message="non-zero exit",
                total_cost_usd=0.0,
                exit_status=self._exit_status,
                stream_json_path=Path("/dev/null"),
                duration_seconds=0.0,
            )
        )


class FakeItemPicker:
    """AsyncMock-based ItemPicker Protocol satisfier."""

    def __init__(self, return_value: Item | None = None) -> None:
        self._return_value = return_value
        self.called_with: list[Item] = []

    async def pick(self, items: Sequence[Item]) -> Item | None:
        self.called_with = list(items)
        return self._return_value


def make_tool_event(tool: str, **input_kwargs: object) -> ToolUseEvent:
    return ToolUseEvent(tool=tool, input=input_kwargs)


def make_needs_refinement_items(ids: list[int]) -> list[Item]:
    """Factory with spaced created_at values for predictable sort order."""
    return [
        make_item(
            item_id=str(i),
            title=f"Needs refinement item {i}",
            labels=("needs-refinement",),
            created_at=_EPOCH + timedelta(hours=idx),
        )
        for idx, i in enumerate(ids)
    ]


class ScriptedInterviewRunner(AgentRunner):
    """Each stream() call yields one AssistantTextEvent + ResultEvent from the scripted list.

    Cycles through responses per turn.
    """

    def __init__(self, responses: list[tuple[str, float]]) -> None:
        self._responses = responses
        self._iter: Iterator[tuple[str, float]] = iter([])
        self._call_count = 0

    async def stream(
        self, prompt: str, *, model: str, cwd: Path | None = None
    ) -> AsyncIterator[RunEvent]:
        from cog.core.runner import AssistantTextEvent

        message, cost = self._responses[self._call_count % len(self._responses)]
        self._call_count += 1
        yield AssistantTextEvent(text=message)
        yield ResultEvent(
            result=RunResult(
                final_message=message,
                total_cost_usd=cost,
                exit_status=0,
                stream_json_path=Path("/dev/null"),
                duration_seconds=0.0,
            )
        )


class ScriptedInputProvider:
    """Each prompt() call returns the next value from the list; None simulates early-end."""

    def __init__(self, replies: list[str | None]) -> None:
        self._replies = list(replies)
        self._index = 0

    async def prompt(self) -> str | None:
        if self._index >= len(self._replies):
            raise AssertionError("ScriptedInputProvider ran out of replies")
        reply = self._replies[self._index]
        self._index += 1
        return reply


class ScriptedRewriteRunner(AgentRunner):
    """Returns a scripted final message with ### Title + ### Body sections."""

    def __init__(self, response: str, cost_usd: float = 0.5) -> None:
        self._response = response
        self._cost = cost_usd

    async def stream(
        self, prompt: str, *, model: str, cwd: Path | None = None
    ) -> AsyncIterator[RunEvent]:
        yield ResultEvent(
            result=RunResult(
                final_message=self._response,
                total_cost_usd=self._cost,
                exit_status=0,
                stream_json_path=Path("/dev/null"),
                duration_seconds=0.0,
            )
        )


@dataclass
class FakeEditor:
    """Simulate $EDITOR. body_after_edit=None means exit-without-save."""

    body_after_edit: str | None
    called_with: list[str] = field(default_factory=list)

    async def edit(self, app: object, initial_text: str, tmp_dir: object) -> str | None:
        self.called_with.append(initial_text)
        return self.body_after_edit


class FakeIssueTracker(IssueTracker):
    """In-memory IssueTracker for UI tests.

    Constructor takes a canned Item list. Set `list_error` or `get_error` to
    make those calls raise TrackerError.
    """

    can_read = True
    can_comment = False
    can_swap_labels = False
    can_create_linked = False

    def __init__(
        self,
        items: list[Item] | None = None,
        *,
        list_error: Exception | None = None,
        get_error: Exception | None = None,
    ) -> None:
        self._items = list(items or [])
        self._list_error = list_error
        self._get_error = get_error
        self.list_calls: list[ItemListFilter | None] = []
        self.get_calls: list[str] = []

    async def list(self, filter: ItemListFilter | None = None) -> ItemListResult:
        self.list_calls.append(filter)
        if self._list_error is not None:
            raise self._list_error
        return ItemListResult(items=list(self._items), total=len(self._items))

    async def list_by_label(self, label: str, *, assignee: str | None = None) -> list[Item]:
        return [i for i in self._items if label in i.labels]

    async def get(self, item_id: str) -> Item:
        self.get_calls.append(item_id)
        if self._get_error is not None:
            raise self._get_error
        for item in self._items:
            if item.item_id == item_id:
                return item
        from cog.core.errors import TrackerError

        raise TrackerError(f"item {item_id} not found")

    async def comment(self, item: Item, body: str) -> None:
        pass

    async def add_label(self, item: Item, label: str) -> None:
        pass

    async def remove_label(self, item: Item, label: str) -> None:
        pass

    async def update_body(self, item: Item, body: str, *, title: str | None = None) -> None:
        pass

    async def ensure_label(
        self,
        name: str,
        *,
        color: str = "cccccc",
        description: str = "",
    ) -> None:
        pass


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
