"""RalphWorkflow: autonomous agent workflow."""

from __future__ import annotations

import asyncio
import os
import re
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from importlib.resources import files
from pathlib import Path
from typing import ClassVar, Literal

import cog.git as git
from cog.checks import RALPH_CHECKS
from cog.core.context import ExecutionContext
from cog.core.errors import (
    CiFixFailedError,
    CiRetryCapExhaustedError,
    CiTimeoutError,
    GitError,
    HostError,
    RebaseUnresolvedError,
    StageError,
    TrackerError,
)
from cog.core.host import GitHost, PrChecks, PullRequest
from cog.core.item import Item
from cog.core.outcomes import Outcome, StageResult
from cog.core.runner import AgentRunner, StatusEvent
from cog.core.stage import Stage
from cog.core.tracker import IssueTracker
from cog.core.workflow import IterationOutcome, StageExecutor, Workflow
from cog.git.worktree import (
    create_worktree,
    discard_worktree,
    is_ahead_of_origin,
    is_dirty,
    push_with_retry,
    remote_branch_exists,
    remove_worktree,
    scan_orphans,
)
from cog.state_paths import project_slug, project_state_dir
from cog.telemetry import TelemetryRecord
from cog.ui.widgets.log_pane import LogPaneWidget


@dataclass(frozen=True)
class RebaseOutcome:
    status: Literal["clean", "conflict"]
    final_message: str = ""  # claude's analysis on conflict; empty on clean


def _parse_float_env(key: str, default: float) -> float:
    try:
        return float(os.environ[key])
    except (KeyError, ValueError):
        return default


def _parse_int_env(key: str, default: int) -> int:
    try:
        return int(os.environ[key])
    except (KeyError, ValueError):
        return default


# attempt_history: list of (attempt_number, tuple_of_check_names)
_AttemptHistory = list[tuple[int, tuple[str, ...]]]


def _dedupe_attempt_checks(attempt_history: _AttemptHistory) -> tuple[str, ...]:
    """Return all check names from attempt history, deduplicated (order-preserving)."""
    seen: set[str] = set()
    result: list[str] = []
    for _, names in attempt_history:
        for name in names:
            if name not in seen:
                seen.add(name)
                result.append(name)
    return tuple(result)


def _format_cap_comment(attempt_history: _AttemptHistory, retries_done: int) -> str:
    """Build the smart cap-exhausted PR comment."""
    check_sets = [set(names) for _, names in attempt_history if names]
    intersection = set.intersection(*check_sets) if len(check_sets) > 1 else set()

    if intersection and len(check_sets) > 1:
        signal = (
            f"⚠ These checks failed on every attempt: {sorted(intersection)} — "
            f"consider whether they're flaky or environment-specific."
        )
    elif len(check_sets) > 1:
        signal = "⚠ Different checks failed on each attempt — fixes may be introducing regressions."
    else:
        signal = "⚠ Could not confirm a fix after retrying."

    lines = [f"🤖 Cog exhausted {retries_done + 1} CI fix attempts:"]
    for n, names in attempt_history:
        label = "initial" if n == 0 else f"fix {n}"
        lines.append(f"- Attempt {n} ({label}): {', '.join(names) if names else '(unknown)'}")
    lines.append(signal)
    return "\n".join(lines)


_DEFAULT_POLL_INTERVAL = 15.0
_DEFAULT_CI_TIMEOUT = 1800.0
_HEARTBEAT_INTERVAL = 60.0


_PRIORITY_RE = re.compile(r"^p(\d+)$")
_SLUG_RE = re.compile(r"[^a-z0-9-]+")
_COLLAPSE_RE = re.compile(r"-+")
_BLOCKER_RE = re.compile(r"\b(?:blocked by|depends on)\s+#(\d+)", re.IGNORECASE)
_ITEM_ID_FROM_PATH_RE = re.compile(r"^(\d+)-")
_SECTION_RE = re.compile(
    r"^###\s+(Summary|Key changes|Test plan)\s*\n(.*?)(?=\n###\s|\Z)",
    re.MULTILINE | re.DOTALL | re.IGNORECASE,
)


class _BranchCase(Enum):
    RESTART = "restart"
    RESUME_LOCAL = "resume_local"
    RECREATE_STALE = "recreate_stale"
    RESUME_REMOTE = "resume_remote"
    FRESH_START = "fresh_start"


class _TeardownAction(Enum):
    REMOVE = "remove"
    PUSH_THEN_REMOVE_OR_STUCK = "push_then_remove"
    PUSH_BEST_EFFORT_THEN_STUCK = "push_then_stuck"
    LEAVE_STUCK = "leave_stuck"


def _priority_tier(item: Item) -> int:
    """Extract lowest pN tier from labels. Items without pN go to tier 999."""
    tiers = [int(m.group(1)) for label in item.labels if (m := _PRIORITY_RE.match(label))]
    return min(tiers) if tiers else 999


def _slugify(title: str) -> str:
    lower = title.lower()
    replaced = _SLUG_RE.sub("-", lower)
    collapsed = _COLLAPSE_RE.sub("-", replaced).strip("-")
    capped = collapsed[:50].rstrip("-")
    return capped or "issue"


def _load_prompt(stage_name: str) -> str:
    return (
        files("cog.prompts.claude.ralph").joinpath(f"{stage_name}.md").read_text(encoding="utf-8")
    )


def _build_prompt(stage_name: str, ctx: ExecutionContext) -> str:
    if ctx.item is None:
        raise AssertionError("RalphWorkflow requires ctx.item to be set")
    item = ctx.item
    parts: list[str] = [_load_prompt(stage_name)]
    parts.append("\n## Your task this iteration\n")
    parts.append(f"Issue #{item.item_id}: {item.title}")
    if ctx.work_branch:
        parts.append(f"Branch: {ctx.work_branch}")
    return "\n".join(parts)


def _make_prompt_source(stage_name: str) -> Callable[[ExecutionContext], str]:
    return lambda ctx: _build_prompt(stage_name, ctx)


def _split_final_message(message: str) -> dict[str, str]:
    sections: dict[str, str] = {}
    for match in _SECTION_RE.finditer(message):
        key = match.group(1).lower().replace(" ", "_")
        sections[key] = match.group(2).strip()
    return sections


class RalphWorkflow(Workflow):
    """Autonomous agent workflow."""

    name: ClassVar[str] = "ralph"
    queue_label: ClassVar[str] = "agent-ready"
    supports_headless: ClassVar[bool] = True
    content_widget_cls = LogPaneWidget
    preflight_checks = RALPH_CHECKS

    def __init__(
        self,
        runner: AgentRunner,
        tracker: IssueTracker,
        host: GitHost | None = None,
        *,
        restart: bool = False,
    ) -> None:
        self._runner = runner
        self._tracker = tracker
        self._host = host
        self._restart = restart
        self._processed_this_loop: set[tuple[str, str]] = set()
        self._ci_retries: dict[str, int] = {}
        # item_ids with known stuck (dirty/unregistered) worktrees — skipped during pick
        self._stuck_worktree_item_ids: set[str] = set()

    async def _apply_orphan_scan(self, project_dir: Path) -> None:
        """Run orphan scan, label stuck items, and populate _stuck_worktree_item_ids."""
        try:
            result = await scan_orphans(project_dir)
        except Exception as e:
            sys.stderr.write(f"warning: orphan scan failed: {e}\n")
            return

        if result.cleaned or result.pushed:
            cleaned = len(result.cleaned)
            pushed = len(result.pushed)
            sys.stderr.write(
                f"-- Cleaned {cleaned} orphan worktree(s), pushed {pushed} commit set(s)\n"
            )

        for stuck in result.dirty:
            if stuck.item_id is not None:
                item_id = str(stuck.item_id)
                self._stuck_worktree_item_ids.add(item_id)
                try:
                    item = await self._tracker.get(item_id)
                    await self._tracker.ensure_label(
                        "agent-failed",
                        color="d93f0b",
                        description="Cog attempted this and hit an error; retry is still eligible",
                    )
                    await self._tracker.add_label(item, "agent-failed")
                    await self._tracker.comment(
                        item,
                        f"🤖 Cog found a stuck worktree from a previous run:\n"
                        f"- Path: `{stuck.path}`\n"
                        f"- Branch: `{stuck.branch}`\n"
                        f"- Reason: {stuck.reason}\n\n"
                        f"Resolve the worktree manually to re-queue this item.",
                    )
                except Exception as e:
                    sys.stderr.write(f"warning: could not label stuck item #{stuck.item_id}: {e}\n")

        for path in result.unregistered:
            item_id_str = _ITEM_ID_FROM_PATH_RE.match(path.name)
            if item_id_str:
                self._stuck_worktree_item_ids.add(item_id_str.group(1))

    async def select_item(self, ctx: ExecutionContext) -> Item | None:
        items = await self._tracker.list_by_label("agent-ready", assignee="@me")
        # NOTE: intentionally do NOT filter by is_deferred here — we re-scan
        # per iteration so items whose blockers have closed become eligible again.
        eligible = [
            item
            for item in items
            if (item.tracker_id, item.item_id) not in self._processed_this_loop
            and not ctx.state_cache.is_processed(item)
            and not self._has_stuck_worktree(ctx, item)
        ]
        eligible.sort(key=lambda i: (_priority_tier(i), i.created_at))

        for candidate in eligible:
            full = await self._tracker.get(candidate.item_id)
            open_blockers = await self._find_open_blockers(full)
            if open_blockers:
                ctx.state_cache.mark_deferred(full, "blocker", [str(b) for b in open_blockers])
                ctx.state_cache.save()
                await self._write_deferred_telemetry(ctx, full, open_blockers)
                self._processed_this_loop.add((full.tracker_id, full.item_id))
                continue
            if ctx.state_cache.is_deferred(full):
                ctx.state_cache.clear_deferral(full)
                ctx.state_cache.save()
            self._processed_this_loop.add((full.tracker_id, full.item_id))
            return full
        return None

    async def _find_open_blockers(self, item: Item) -> list[int]:
        """Scan body + comments for 'blocked by #N' / 'depends on #N' references.
        Returns item_ids of referenced blockers that are currently open, sorted."""
        text_sources = [item.body] + [c.body for c in item.comments]
        referenced = {
            int(m.group(1)) for source in text_sources for m in _BLOCKER_RE.finditer(source)
        }
        open_blockers: list[int] = []
        for blocker_id in sorted(referenced):
            try:
                blocker = await self._tracker.get(str(blocker_id))
            except TrackerError:
                continue  # ghost reference; don't defer on an item we can't fetch
            if blocker.state == "open":
                open_blockers.append(blocker_id)
        return open_blockers

    async def _write_deferred_telemetry(
        self,
        ctx: ExecutionContext,
        item: Item,
        open_blockers: list[int],
    ) -> None:
        if ctx.telemetry is None:
            return
        record = TelemetryRecord.build(
            project=project_slug(ctx.project_dir),
            workflow=self.name,
            item=item,
            outcome="deferred-by-blocker",
            results=[],
            branch=None,
            pr_url=None,
            duration_seconds=0.0,
            error=f"blocked by: {', '.join(f'#{b}' for b in open_blockers)}",
        )
        await ctx.telemetry.write(record)

    def _has_stuck_worktree(self, ctx: ExecutionContext, item: Item) -> bool:
        """True if a dirty/unregistered worktree exists for this item on disk.

        Re-checks disk each pick so externally resolving the worktree (deleting
        the dir, finishing a manual cleanup) re-eligibles the item without
        restarting cog.
        """
        slug = _slugify(item.title)
        wt_path = ctx.project_dir / ".cog" / "worktrees" / f"{item.item_id}-{slug}"
        if not wt_path.exists():
            self._stuck_worktree_item_ids.discard(item.item_id)
            return False
        return True

    async def _classify_branch(
        self, ctx: ExecutionContext, branch: str, default: str
    ) -> _BranchCase:
        if self._restart:
            return _BranchCase.RESTART
        if await git.branch_exists(ctx.project_dir, branch):
            ahead = await git.commits_between(ctx.project_dir, f"origin/{default}", branch)
            return _BranchCase.RESUME_LOCAL if ahead > 0 else _BranchCase.RECREATE_STALE
        if await remote_branch_exists(ctx.project_dir, branch):
            return _BranchCase.RESUME_REMOTE
        return _BranchCase.FRESH_START

    async def iteration_start(self, ctx: ExecutionContext) -> None:
        assert ctx.item is not None, "RalphWorkflow.iteration_start requires ctx.item"
        item = ctx.item
        default = await git.default_branch(ctx.project_dir)
        await git.fetch_origin(ctx.project_dir)

        slug = _slugify(item.title)
        work_branch = f"cog/{item.item_id}-{slug}"
        wt_path = ctx.project_dir / ".cog" / "worktrees" / f"{item.item_id}-{slug}"

        case = await self._classify_branch(ctx, work_branch, default)
        match case:
            case _BranchCase.RESTART:
                if wt_path.exists():
                    await discard_worktree(ctx.project_dir, wt_path)
                if await git.branch_exists(ctx.project_dir, work_branch):
                    await git.delete_branch(ctx.project_dir, work_branch)
                await create_worktree(
                    ctx.project_dir,
                    wt_path,
                    work_branch,
                    start_point=f"origin/{default}",
                    create_branch=True,
                )
            case _BranchCase.RESUME_LOCAL:
                await create_worktree(
                    ctx.project_dir,
                    wt_path,
                    work_branch,
                    start_point=work_branch,
                    create_branch=False,
                )
            case _BranchCase.RECREATE_STALE:
                await git.delete_branch(ctx.project_dir, work_branch)
                await create_worktree(
                    ctx.project_dir,
                    wt_path,
                    work_branch,
                    start_point=f"origin/{default}",
                    create_branch=True,
                )
            case _BranchCase.RESUME_REMOTE:
                await create_worktree(
                    ctx.project_dir,
                    wt_path,
                    work_branch,
                    start_point=f"origin/{work_branch}",
                    create_branch=True,
                )
            case _BranchCase.FRESH_START:
                await create_worktree(
                    ctx.project_dir,
                    wt_path,
                    work_branch,
                    start_point=f"origin/{default}",
                    create_branch=True,
                )

        ctx.resumed = case in (_BranchCase.RESUME_LOCAL, _BranchCase.RESUME_REMOTE)
        ctx.work_branch = work_branch
        ctx.worktree_path = wt_path
        self._stuck_worktree_item_ids.discard(item.item_id)

    async def _teardown_action(
        self, wt_path: Path, branch: str, outcome: IterationOutcome
    ) -> _TeardownAction:
        dirty = await is_dirty(wt_path)
        ahead = await is_ahead_of_origin(wt_path, branch)
        if outcome is IterationOutcome.success:
            return _TeardownAction.LEAVE_STUCK if dirty else _TeardownAction.REMOVE
        if dirty:
            if ahead and outcome is not IterationOutcome.noop:
                return _TeardownAction.PUSH_BEST_EFFORT_THEN_STUCK
            return _TeardownAction.LEAVE_STUCK
        return _TeardownAction.PUSH_THEN_REMOVE_OR_STUCK if ahead else _TeardownAction.REMOVE

    async def iteration_end(self, ctx: ExecutionContext, outcome: IterationOutcome) -> None:
        wt_path, branch = ctx.worktree_path, ctx.work_branch
        if wt_path is None or not wt_path.exists() or branch is None:
            return

        action = await self._teardown_action(wt_path, branch, outcome)
        match action:
            case _TeardownAction.REMOVE:
                await self._remove_worktree_safe(ctx.project_dir, wt_path)
            case _TeardownAction.PUSH_THEN_REMOVE_OR_STUCK:
                if await self._push_worktree_safe(ctx, wt_path, branch):
                    await self._remove_worktree_safe(ctx.project_dir, wt_path)
                else:
                    self._mark_stuck(ctx)
            case _TeardownAction.PUSH_BEST_EFFORT_THEN_STUCK:
                await self._push_worktree_safe(ctx, wt_path, branch)
                self._mark_stuck(ctx)
            case _TeardownAction.LEAVE_STUCK:
                if outcome is IterationOutcome.success:
                    await self._emit(
                        ctx,
                        StatusEvent(
                            message=f"⚠ worktree {wt_path} is dirty after success; leaving in place"
                        ),
                    )
                self._mark_stuck(ctx)

    def _mark_stuck(self, ctx: ExecutionContext) -> None:
        if ctx.item is not None:
            self._stuck_worktree_item_ids.add(ctx.item.item_id)

    async def _push_worktree_safe(self, ctx: ExecutionContext, wt_path: Path, branch: str) -> bool:
        """Push branch from worktree; returns True on success."""
        try:
            await push_with_retry(wt_path, branch)
            return True
        except GitError as e:
            sys.stderr.write(f"warning: push failed for {branch}: {e}\n")
            return False

    async def _remove_worktree_safe(self, project_dir: Path, wt_path: Path) -> None:
        try:
            await remove_worktree(project_dir, wt_path)
        except GitError as e:
            sys.stderr.write(f"warning: could not remove worktree {wt_path}: {e}\n")

    def stages(self, ctx: ExecutionContext) -> list[Stage]:
        cwd = ctx.worktree_path
        return [
            Stage(
                name="build",
                prompt_source=_make_prompt_source("build"),
                model=os.environ.get("COG_RALPH_BUILD_MODEL", "claude-sonnet-4-6"),
                runner=self._runner,
                tolerate_failure=False,
                cwd=cwd,
            ),
            Stage(
                name="review",
                prompt_source=_make_prompt_source("review"),
                model=os.environ.get("COG_RALPH_REVIEW_MODEL", "claude-opus-4-7"),
                runner=self._runner,
                tolerate_failure=False,
                cwd=cwd,
            ),
            Stage(
                name="document",
                prompt_source=_make_prompt_source("document"),
                model=os.environ.get("COG_RALPH_DOCUMENT_MODEL", "claude-sonnet-4-6"),
                runner=self._runner,
                # document failures flagged for PR footer by #14; don't abort
                tolerate_failure=True,
                cwd=cwd,
            ),
        ]

    async def classify_outcome(self, ctx: ExecutionContext, results: list[StageResult]) -> Outcome:
        total_commits = sum(r.commits_created for r in results)
        return "success" if total_commits > 0 else "noop"

    async def finalize_success(
        self,
        ctx: ExecutionContext,
        results: list[StageResult],
    ) -> None:
        assert ctx.item is not None and ctx.work_branch is not None
        assert self._host is not None, "RalphWorkflow.finalize_success requires host"

        rebase = await self._rebase_before_push(ctx)
        if rebase.status == "conflict":
            await self._handle_rebase_conflict(ctx, results, rebase.final_message)
            return

        try:
            await self._host.push_branch(ctx.work_branch)
        except HostError as e:
            await self._handle_push_failed(ctx, results, e)
            return

        existing = await self._host.get_pr_for_branch(ctx.work_branch)
        body = self._build_pr_body(ctx, results)
        if existing is None:
            title = f"{ctx.item.title} (#{ctx.item.item_id})"
            pr = await self._host.create_pr(head=ctx.work_branch, title=title, body=body)
        else:
            await self._host.update_pr(existing.number, body=body)
            pr = existing

        await self._tracker.comment(ctx.item, f"🤖 Cog opened a PR for this: {pr.url}")

        try:
            checks = await self._wait_for_ci(ctx, pr)
        except CiTimeoutError as e:
            await self._abandon_ci(ctx, results, pr, [(0, ())], claude_analysis=None, cause=e)
            return

        if checks.all_passed:
            await self._mark_ci_success(ctx, results, pr)
        else:
            await self._handle_ci_failure(ctx, results, pr, checks)

    async def _wait_for_ci(self, ctx: ExecutionContext, pr: PullRequest) -> PrChecks:
        poll_interval = _parse_float_env("COG_CI_POLL_INTERVAL_SECONDS", _DEFAULT_POLL_INTERVAL)
        ci_timeout = _parse_float_env("COG_CI_TIMEOUT_SECONDS", _DEFAULT_CI_TIMEOUT)
        await self._emit(ctx, StatusEvent(message=f"⏳ Waiting for CI on PR #{pr.number}..."))

        started = time.monotonic()
        last_heartbeat = started
        checks: PrChecks | None = None

        try:
            async with asyncio.timeout(ci_timeout):
                while True:
                    checks = await self._host.get_pr_checks(pr.number)  # type: ignore[union-attr]
                    if checks.runs and not checks.pending:
                        break

                    now = time.monotonic()
                    if now - last_heartbeat >= _HEARTBEAT_INTERVAL:
                        elapsed_m = int((now - started) / 60)
                        if not checks.runs:
                            message = f"⏳ CI: waiting for checks to begin ({elapsed_m}m elapsed)"
                        else:
                            passed = sum(1 for r in checks.runs if r.state == "passed")
                            total = len(checks.runs)
                            pending = sum(1 for r in checks.runs if r.state == "pending")
                            message = (
                                f"⏳ CI: {passed}/{total} passed, "
                                f"{pending} pending ({elapsed_m}m elapsed)"
                            )
                        await self._emit(ctx, StatusEvent(message=message))
                        last_heartbeat = now

                    await asyncio.sleep(poll_interval)
        except TimeoutError:
            raise CiTimeoutError(timeout_seconds=ci_timeout) from None

        assert checks is not None
        if checks.all_passed:
            await self._emit(ctx, StatusEvent(message="✓ All CI checks passed"))
        else:
            failed_names = ", ".join(r.name for r in checks.failed)
            await self._emit(ctx, StatusEvent(message=f"✗ CI failed: {failed_names}"))
        return checks

    @staticmethod
    async def _emit(ctx: ExecutionContext, event: StatusEvent) -> None:
        if ctx.event_sink is not None:
            await ctx.event_sink.emit(event)

    async def _mark_ci_success(
        self,
        ctx: ExecutionContext,
        results: list[StageResult],
        pr: PullRequest,
        *,
        retry_count: int = 0,
        ci_failed_checks: tuple[str, ...] = (),
    ) -> None:
        await self._tracker.remove_label(ctx.item, "agent-ready")  # type: ignore[arg-type]
        try:
            await self._tracker.remove_label(ctx.item, "agent-failed")  # type: ignore[arg-type]
        except TrackerError:
            pass
        ctx.state_cache.mark_processed(ctx.item, "success")  # type: ignore[arg-type]
        ctx.state_cache.save()
        await self._write_telemetry(
            ctx,
            results,
            "success",
            pr_url=pr.url,
            retry_count=retry_count,
            ci_failed_checks=ci_failed_checks,
        )
        await self.write_report(ctx, results, "success", error=None)

    async def _handle_ci_failure(
        self,
        ctx: ExecutionContext,
        results: list[StageResult],
        pr: PullRequest,
        checks: PrChecks,
    ) -> None:
        retries_done = self._ci_retries.get(ctx.item.item_id, 0)  # type: ignore[union-attr]
        max_retries = _parse_int_env("COG_CI_MAX_RETRIES", 2)

        attempt_history: _AttemptHistory = [(0, tuple(r.name for r in checks.failed))]

        while retries_done < max_retries:
            retries_done += 1
            self._ci_retries[ctx.item.item_id] = retries_done  # type: ignore[union-attr]

            fix_result = await self._run_ci_fix_stage(
                ctx, pr, checks, retries_done, attempt_history
            )

            if fix_result.commits_created == 0:
                await self._abandon_ci(
                    ctx,
                    results,
                    pr,
                    attempt_history,
                    initial_checks=checks,
                    claude_analysis=fix_result.final_message,
                    cause=CiFixFailedError(reason="no-commit"),
                )
                return

            try:
                await self._host.push_branch(ctx.work_branch)  # type: ignore[union-attr,arg-type]
            except HostError as e:
                await self._handle_push_failed(ctx, results, e)
                return

            try:
                checks = await self._wait_for_ci(ctx, pr)
            except CiTimeoutError as e:
                attempt_history.append((retries_done, ()))
                await self._abandon_ci(
                    ctx,
                    results,
                    pr,
                    attempt_history,
                    claude_analysis=None,
                    cause=e,
                )
                return

            if checks.all_passed:
                await self._mark_ci_success(
                    ctx,
                    results,
                    pr,
                    retry_count=retries_done,
                    ci_failed_checks=_dedupe_attempt_checks(attempt_history),
                )
                return
            attempt_history.append((retries_done, tuple(r.name for r in checks.failed)))

        await self._abandon_ci(
            ctx,
            results,
            pr,
            attempt_history,
            claude_analysis=None,
            cause=CiRetryCapExhaustedError(attempts=retries_done + 1),
        )

    async def _run_ci_fix_stage(
        self,
        ctx: ExecutionContext,
        pr: PullRequest,
        checks: PrChecks,
        attempt_number: int,
        attempt_history: _AttemptHistory,
    ) -> StageResult:
        stage = Stage(
            name=f"ci-fix-{attempt_number}",
            prompt_source=lambda c: self._build_ci_fix_prompt(
                c, pr, checks, attempt_number, attempt_history
            ),
            model=os.environ.get("COG_RALPH_BUILD_MODEL", "claude-sonnet-4-6"),
            runner=self._runner,
            tolerate_failure=False,
            cwd=ctx.worktree_path,
        )
        return await StageExecutor()._run_stage(stage, ctx)

    def _build_ci_fix_prompt(
        self,
        ctx: ExecutionContext,
        pr: PullRequest,
        checks: PrChecks,
        attempt_number: int,
        attempt_history: _AttemptHistory,
    ) -> str:
        parts: list[str] = [_load_prompt("ci_fix")]
        parts.append("\n## Your task this iteration\n")
        if ctx.item:
            parts.append(f"Issue #{ctx.item.item_id}: {ctx.item.title}")
        if ctx.work_branch:
            parts.append(f"Branch: {ctx.work_branch}")
        parts.append(f"PR: {pr.url}\n")
        parts.append("\n## Failing checks\n")
        for r in checks.failed:
            parts.append(f"- **{r.name}**: {r.link}")
        if attempt_number > 1:
            parts.append("\n## Previous attempts\n")
            for n, names in attempt_history:
                check_str = ", ".join(names) if names else "(unknown)"
                parts.append(f"- Attempt {n}: failed on {check_str}")
        return "\n".join(parts)

    async def _abandon_ci(
        self,
        ctx: ExecutionContext,
        results: list[StageResult],
        pr: PullRequest,
        attempt_history: _AttemptHistory,
        *,
        initial_checks: PrChecks | None = None,
        claude_analysis: str | None,
        cause: Exception,
    ) -> None:
        assert ctx.item is not None
        assert self._host is not None

        if isinstance(cause, CiRetryCapExhaustedError):
            retries_done = self._ci_retries.get(ctx.item.item_id, 0)
            pr_comment = _format_cap_comment(attempt_history, retries_done)
        else:
            lines = ["🤖 Cog: CI failed on this PR. See:"]
            if initial_checks is not None:
                for r in initial_checks.failed:
                    lines.append(f"- **{r.name}**: {r.link}")
            elif attempt_history and attempt_history[0][1]:
                for name in attempt_history[0][1]:
                    lines.append(f"- **{name}**")
            if claude_analysis:
                lines.append(f"\nClaude's analysis:\n\n> {claude_analysis[:2000]}")
            pr_comment = "\n".join(lines)

        await self._host.comment_on_pr(pr.number, pr_comment)
        await self._tracker.comment(
            ctx.item,
            f"🤖 Cog opened PR #{pr.number} but CI failed and could not be automatically fixed. "
            f"`agent-failed` applied.",
        )
        await self._tracker.ensure_label(
            "agent-failed",
            color="d93f0b",
            description="Cog attempted this and hit an error; retry is still eligible",
        )
        await self._tracker.add_label(ctx.item, "agent-failed")
        await self._tracker.remove_label(ctx.item, "agent-ready")
        ctx.state_cache.mark_processed(ctx.item, "ci-failed")
        ctx.state_cache.save()
        await self._write_telemetry(
            ctx,
            results,
            "ci-failed",
            pr_url=pr.url,
            error=cause,
            cause_class=type(cause).__name__,
            retry_count=self._ci_retries.get(ctx.item.item_id, 0),
            ci_failed_checks=_dedupe_attempt_checks(attempt_history),
        )
        await self.write_report(ctx, results, "error", error=cause)

    async def finalize_noop(
        self,
        ctx: ExecutionContext,
        results: list[StageResult],
    ) -> None:
        assert ctx.item is not None
        build = next((r for r in results if r.stage.name == "build"), None)
        explanation = (
            build.final_message
            if build and build.final_message.strip()
            else "(no explanation provided)"
        )
        await self._tracker.comment(
            ctx.item,
            f"🤖 Cog attempted this but did not commit.\n\n{explanation}",
        )
        await self._tracker.ensure_label(
            "agent-abandoned",
            color="ededed",
            description="Cog attempted this but made no changes",
        )
        await self._tracker.add_label(ctx.item, "agent-abandoned")
        await self._tracker.remove_label(ctx.item, "agent-ready")
        try:
            await self._tracker.remove_label(ctx.item, "agent-failed")
        except TrackerError:
            pass
        ctx.state_cache.mark_processed(ctx.item, "no-op")
        ctx.state_cache.save()
        await self._write_telemetry(ctx, results, "no-op")
        await self.write_report(ctx, results, "noop", error=None)

    async def finalize_error(
        self,
        ctx: ExecutionContext,
        error: Exception,
        results: list[StageResult],
    ) -> None:
        assert ctx.item is not None
        if isinstance(error, StageError):
            summary = f"Stage '{error.stage.name}' failed"
            tail = (
                error.result.final_message[-2000:]
                if error.result and error.result.final_message
                else ""
            )
        else:
            summary = f"{type(error).__name__}: {error}"
            tail = ""
        body = f"🤖 Cog encountered an error:\n\n```\n{summary}\n```"
        if tail:
            body += f"\n\nLast output:\n\n```\n{tail}\n```"
        try:
            await self._tracker.comment(ctx.item, body)
        except Exception:
            print(
                f"warning: failed to comment error on #{ctx.item.item_id}",
                file=sys.stderr,
            )
        # KEEP agent-ready label — item stays eligible for resume.
        # Don't mark processed — item stays eligible.
        await self._tracker.ensure_label(
            "agent-failed",
            color="d93f0b",
            description="Cog attempted this and hit an error; retry is still eligible",
        )
        await self._tracker.add_label(ctx.item, "agent-failed")
        await self._write_telemetry(ctx, results, "error", error=error)
        await self.write_report(ctx, results, "error", error=error)

    async def write_report(
        self,
        ctx: ExecutionContext,
        results: list[StageResult],
        outcome: Literal["success", "noop", "error"],
        *,
        error: Exception | None = None,
    ) -> Path | None:
        assert ctx.item is not None
        from datetime import UTC, datetime

        ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        item_slug = f"{ctx.item.item_id}-{_slugify(ctx.item.title)}"
        reports_dir = project_state_dir(ctx.project_dir) / "reports"
        reports_dir.mkdir(parents=True, exist_ok=True)
        report_path = reports_dir / f"{ts}-ralph-{item_slug}.md"

        build = next((r for r in results if r.stage.name == "build"), None)
        _sections = _split_final_message(build.final_message if build else "")
        summary = _sections.get("summary") or (build.final_message.strip() if build else "")
        test_plan = _sections.get("test_plan") or "- [ ] Manual verification of the change"

        pr_url: str | None = None
        if outcome == "success" and ctx.work_branch and self._host is not None:
            try:
                pr = await self._host.get_pr_for_branch(ctx.work_branch)
                if pr is not None:
                    pr_url = pr.url
            except Exception:
                pass

        total_cost = sum(r.cost_usd for r in results)
        total_duration = sum(r.duration_seconds for r in results)

        lines: list[str] = [f"# Ralph Report: {ctx.item.title} (#{ctx.item.item_id})\n"]
        if pr_url:
            lines.append(f"**PR:** {pr_url}\n")
        lines.append(f"## Summary\n\n{summary}\n")
        lines.append(f"## Test plan\n\n{test_plan}\n")
        lines.append("## Stages\n")
        lines.append("| Stage | Model | Duration (s) | Cost ($) | Exit | Commits | Error |")
        lines.append("|-------|-------|-------------|----------|------|---------|-------|")
        for r in results:
            err_str = str(r.error) if r.error else ""
            lines.append(
                f"| {r.stage.name} | {r.stage.model} | {r.duration_seconds:.1f}"
                f" | {r.cost_usd:.4f} | {r.exit_status} | {r.commits_created}"
                f" | {err_str} |"
            )
        lines.append("")

        if ctx.work_branch:
            try:
                git_dir = ctx.worktree_path or ctx.project_dir
                default = await git.default_branch(ctx.project_dir)
                shas = await git.log_short_shas(
                    git_dir,
                    f"origin/{default}..HEAD",
                )
                if shas:
                    lines.append("## Commits\n")
                    lines.extend(f"- {sha}" for sha in shas)
                    lines.append("")
            except Exception:
                pass

        lines.append(f"## Outcome\n\n**{outcome}**")
        if error:
            lines.append(f"\nError: {error}")
        lines.append(f"\nTotal cost: ${total_cost:.4f} | Duration: {total_duration:.1f}s")

        report_path.write_text("\n".join(lines), encoding="utf-8")
        return report_path

    def _build_pr_body(self, ctx: ExecutionContext, results: list[StageResult]) -> str:
        build = next((r for r in results if r.stage.name == "build"), None)
        sections = _split_final_message(build.final_message if build else "")
        summary = sections.get("summary") or (build.final_message.strip() if build else "")
        key_changes = sections.get("key_changes")
        test_plan = sections.get("test_plan") or "- [ ] Manual verification of the change"
        total_cost = sum(r.cost_usd for r in results)

        body = f"## Summary\n\n{summary}\n\n"
        if key_changes:
            body += f"## Key changes\n\n{key_changes}\n\n"
        body += (
            f"## Closes\n\nCloses #{ctx.item.item_id}\n\n"  # type: ignore[union-attr]
            f"## Test plan\n\n{test_plan}\n\n"
            f"---\n🤖 Generated by cog. Iteration cost: ${total_cost:.3f}\n"
        )
        doc = next((r for r in results if r.stage.name == "document"), None)
        if doc is not None and doc.error is not None:
            body += f"\n⚠ Document stage failed: {doc.error}. Docs may be out of date.\n"
        return body

    async def _rebase_before_push(self, ctx: ExecutionContext) -> RebaseOutcome:
        assert ctx.work_branch is not None
        git_dir = ctx.worktree_path or ctx.project_dir
        default = await git.default_branch(ctx.project_dir)
        await git.fetch_origin(ctx.project_dir)

        # Cheap pre-check: skip stage invocation when work branch already
        # contains everything from origin/<default>. Common no-op on quiet
        # iterations; saves ~$0.05-0.10 per iteration.
        try:
            behind = await git.commits_between(git_dir, ctx.work_branch, f"origin/{default}")
        except GitError:
            # Transient git error — assume rebase is needed, fall through.
            behind = 1

        if behind == 0:
            return RebaseOutcome(status="clean")

        stage = Stage(
            name="rebase",
            prompt_source=_make_prompt_source("rebase"),
            model=os.environ.get("COG_RALPH_BUILD_MODEL", "claude-sonnet-4-6"),
            runner=self._runner,
            tolerate_failure=False,
            cwd=ctx.worktree_path,
        )
        result = await StageExecutor()._run_stage(stage, ctx)

        if await git.rebase_in_progress(git_dir):
            # Claude exited mid-rebase — safety net abort.
            await git.rebase_abort(git_dir)
            return RebaseOutcome(status="conflict", final_message=result.final_message)

        return RebaseOutcome(status="clean")

    async def _handle_rebase_conflict(
        self,
        ctx: ExecutionContext,
        results: list[StageResult],
        claude_final_message: str,
    ) -> None:
        assert ctx.item is not None
        default = await git.default_branch(ctx.project_dir)
        body = (
            f"🤖 Cog finished stages but could not rebase onto the latest "
            f"`origin/{default}` due to conflicts claude couldn't semantically "
            f"resolve.\n\n"
            f"Claude's analysis:\n\n> {claude_final_message}\n\n"
            f"The work branch is intact locally. Cog will retry on the next "
            f"`cog ralph` invocation — the conflict may resolve once main "
            f"advances further, or a human can resolve manually and re-run."
        )
        await self._tracker.comment(ctx.item, body)

        # Additive agent-failed signal (matches #51 pattern)
        await self._tracker.ensure_label(
            "agent-failed",
            color="d93f0b",
            description="Cog attempted this and hit an error; retry is still eligible",
        )
        await self._tracker.add_label(ctx.item, "agent-failed")

        # Keep agent-ready — user / next iteration re-triggers.
        # Don't mark_processed — item stays eligible.
        # Prevent same-loop re-pick.
        self._processed_this_loop.add((ctx.item.tracker_id, ctx.item.item_id))

        err = RebaseUnresolvedError(final_message=claude_final_message)
        await self._write_telemetry(
            ctx,
            results,
            "rebase-conflict",
            error=err,
            cause_class=type(err).__name__,
        )
        await self.write_report(ctx, results, "error", error=err)

    async def _handle_push_failed(
        self,
        ctx: ExecutionContext,
        results: list[StageResult],
        error: HostError,
    ) -> None:
        assert ctx.item is not None and ctx.work_branch is not None
        body = (
            f"🤖 Cog finished stages but failed to push `{ctx.work_branch}`.\n\n"
            f"Error:\n```\n{error}\n```\n\n"
            f"Work is on your local branch. To push manually:\n"
            f"```\ngit push -u origin {ctx.work_branch}\n```"
        )
        try:
            await self._tracker.comment(ctx.item, body)
        except Exception:
            print(
                f"warning: failed to comment push-fail on #{ctx.item.item_id}",
                file=sys.stderr,
            )
        # KEEP agent-ready — next run will resume + retry push.
        # Don't mark processed — local commits exist; resume path handles it.
        await self._tracker.ensure_label(
            "agent-failed",
            color="d93f0b",
            description="Cog attempted this and hit an error; retry is still eligible",
        )
        await self._tracker.add_label(ctx.item, "agent-failed")
        await self._write_telemetry(ctx, results, "push-failed", error=error)
        await self.write_report(ctx, results, "error", error=error)

    async def _write_telemetry(
        self,
        ctx: ExecutionContext,
        results: list[StageResult],
        outcome: str,
        *,
        pr_url: str | None = None,
        error: Exception | None = None,
        cause_class: str | None = None,
        retry_count: int = 0,
        ci_failed_checks: tuple[str, ...] = (),
    ) -> None:
        error_str: str | None = None
        resolved_cause_class = cause_class
        if error is not None:
            error_str = str(error)
            if (
                resolved_cause_class is None
                and isinstance(error, StageError)
                and error.cause is not None
            ):
                resolved_cause_class = type(error.cause).__name__
        if ctx.telemetry is None:
            return
        record = TelemetryRecord.build(
            project=project_slug(ctx.project_dir),
            workflow=self.name,
            item=ctx.item,  # type: ignore[arg-type]
            outcome=outcome,  # type: ignore[arg-type]
            results=results,
            branch=ctx.work_branch,
            pr_url=pr_url,
            duration_seconds=sum(r.duration_seconds for r in results),
            error=error_str,
            cause_class=resolved_cause_class,
            resumed=ctx.resumed,
            retry_count=retry_count,
            ci_failed_checks=ci_failed_checks,
        )
        await ctx.telemetry.write(record)
