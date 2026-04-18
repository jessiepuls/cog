import contextlib
import fcntl
import json
import os
import sys
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cog.core.host import GitHost
from cog.core.item import Item
from cog.core.tracker import IssueTracker


@dataclass(frozen=True)
class ProcessedRecord:
    tracker_id: str
    item_id: str
    outcome: str  # from TelemetryOutcome vocabulary
    ts: datetime  # tz-aware UTC


@dataclass(frozen=True)
class DeferredRecord:
    tracker_id: str
    item_id: str
    reason: str  # e.g., "blocker"
    blockers: tuple[str, ...]  # item_ids of blocking issues
    ts: datetime


class JsonFileStateCache:
    """Satisfies cog.core.state.StateCache Protocol structurally.

    Mutations are in-memory only; callers must invoke save() to persist.
    Backing file lives at ~/.local/state/cog/<slug>/state.json (path set at construction).
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._processed: dict[tuple[str, str], ProcessedRecord] = {}
        self._deferred: dict[tuple[str, str], DeferredRecord] = {}
        self._last_run: datetime | None = None
        self._loaded = False
        self._corrupt = False

    # --- file lifecycle ---

    def load(self) -> None:
        """Read state from disk. Missing/corrupt file → empty state + `was_corrupt()` set."""
        self._loaded = True
        if not self._path.exists():
            self._corrupt = False
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            if data.get("schema_version") != 1:
                raise ValueError(f"unsupported schema_version: {data.get('schema_version')!r}")
            for rec in data.get("processed_items", []):
                p = ProcessedRecord(
                    tracker_id=rec["tracker_id"],
                    item_id=rec["item_id"],
                    outcome=rec["outcome"],
                    ts=datetime.fromisoformat(rec["ts"]),
                )
                self._processed[(p.tracker_id, p.item_id)] = p
            for rec in data.get("deferred_items", []):
                d = DeferredRecord(
                    tracker_id=rec["tracker_id"],
                    item_id=rec["item_id"],
                    reason=rec["reason"],
                    blockers=tuple(rec.get("blockers", [])),
                    ts=datetime.fromisoformat(rec["ts"]),
                )
                self._deferred[(d.tracker_id, d.item_id)] = d
            self._last_run = (
                datetime.fromisoformat(data["last_run"]) if data.get("last_run") else None
            )
            self._corrupt = False
        except (json.JSONDecodeError, ValueError, KeyError, TypeError) as e:
            print(f"warning: state file corrupt ({e}); starting empty", file=sys.stderr)
            self._processed.clear()
            self._deferred.clear()
            self._last_run = None
            self._corrupt = True

    def was_corrupt(self) -> bool:
        return self._corrupt

    def is_empty(self) -> bool:
        return not self._processed and not self._deferred

    def save(self) -> None:
        """Flush to disk. Flock-serialized, atomic (tempfile + os.replace). Raises on failure."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._last_run = datetime.now(UTC)
        lock_path = self._path.with_name(self._path.name + ".lock")
        with lock_path.open("a") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            fd, tmp_name = tempfile.mkstemp(
                suffix=".json.tmp",
                dir=str(self._path.parent),
                text=False,
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as tmp:
                    json.dump(self._serialize(), tmp, indent=2, sort_keys=True)
                    tmp.flush()
                    os.fsync(tmp.fileno())
                os.replace(tmp_name, self._path)
            except Exception:
                with contextlib.suppress(FileNotFoundError):
                    os.unlink(tmp_name)
                raise

    def _serialize(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "processed_items": [
                {
                    "tracker_id": p.tracker_id,
                    "item_id": p.item_id,
                    "outcome": p.outcome,
                    "ts": p.ts.isoformat(),
                }
                for p in sorted(self._processed.values(), key=lambda r: (r.tracker_id, r.item_id))
            ],
            "deferred_items": [
                {
                    "tracker_id": d.tracker_id,
                    "item_id": d.item_id,
                    "reason": d.reason,
                    "blockers": list(d.blockers),
                    "ts": d.ts.isoformat(),
                }
                for d in sorted(self._deferred.values(), key=lambda r: (r.tracker_id, r.item_id))
            ],
            "last_run": self._last_run.isoformat() if self._last_run else None,
        }

    # --- Protocol methods ---

    def is_processed(self, item: Item) -> bool:
        """True iff item has a processed record AND item.updated_at <= record.ts (revival rule)."""
        rec = self._processed.get((item.tracker_id, item.item_id))
        if rec is None:
            return False
        return item.updated_at <= rec.ts

    def mark_processed(self, item: Item, outcome: str) -> None:
        self._processed[(item.tracker_id, item.item_id)] = ProcessedRecord(
            tracker_id=item.tracker_id,
            item_id=item.item_id,
            outcome=outcome,
            ts=datetime.now(UTC),
        )

    def is_deferred(self, item: Item) -> bool:
        return (item.tracker_id, item.item_id) in self._deferred

    def mark_deferred(self, item: Item, reason: str, blockers: list[str]) -> None:
        self._deferred[(item.tracker_id, item.item_id)] = DeferredRecord(
            tracker_id=item.tracker_id,
            item_id=item.item_id,
            reason=reason,
            blockers=tuple(blockers),
            ts=datetime.now(UTC),
        )

    def clear_deferral(self, item: Item) -> None:
        self._deferred.pop((item.tracker_id, item.item_id), None)

    # --- recovery (not part of Protocol) ---

    async def recover_from_remote(
        self,
        tracker: IssueTracker,
        host: GitHost,
        queue_label: str,
    ) -> None:
        """Best-effort: scan eligible-queue items; any item with an open PR mentioning it
        is marked as processed with outcome='success'. Partial recovery is acceptable —
        per-item failures are logged and skipped.
        """
        items = await tracker.list_by_label(queue_label, assignee="@me")
        for item in items:
            try:
                prs = await host.get_open_prs_mentioning_item(item)
            except Exception as e:
                print(
                    f"warning: could not fetch PRs for {item.tracker_id}#{item.item_id}: {e}",
                    file=sys.stderr,
                )
                continue
            if prs:
                self.mark_processed(item, outcome="success")
