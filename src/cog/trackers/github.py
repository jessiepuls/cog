import asyncio
import json
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from subprocess import PIPE
from typing import Any, ClassVar

from cog.core.errors import TrackerError
from cog.core.item import Comment, Item
from cog.core.tracker import IssueTracker


class GitHubIssueTracker(IssueTracker):
    can_read: ClassVar[bool] = True
    can_comment: ClassVar[bool] = True
    can_swap_labels: ClassVar[bool] = True
    can_create_linked: ClassVar[bool] = False

    def __init__(self, project_dir: Path) -> None:
        self._project_dir = project_dir
        self._tracker_id: str | None = None

    async def list_by_label(self, label: str, *, assignee: str | None = None) -> list[Item]:
        """Returns metadata-only Items. `comments` is always an empty tuple.

        Callers needing comments should call `get(item_id)` for the full Item.
        """
        args = [
            "issue",
            "list",
            "--label",
            label,
            "--state",
            "open",
            "--json",
            "number,title,body,labels,createdAt,updatedAt,url",
        ]
        if assignee is not None:
            args += ["--assignee", assignee]
        data = await self._gh_json(args)
        tracker_id = await self._tracker_id_cached()
        return [self._to_item(record, tracker_id, with_comments=False) for record in data]

    async def get(self, item_id: str) -> Item:
        """Full Item with comments populated."""
        data = await self._gh_json(
            [
                "issue",
                "view",
                item_id,
                "--json",
                "number,title,body,labels,comments,state,createdAt,updatedAt,url",
            ]
        )
        tracker_id = await self._tracker_id_cached()
        return self._to_item(data, tracker_id, with_comments=True)

    async def comment(self, item: Item, body: str) -> None:
        await self._gh_run(["issue", "comment", item.item_id, "--body", body])

    async def add_label(self, item: Item, label: str) -> None:
        await self._gh_run(["issue", "edit", item.item_id, "--add-label", label])

    async def remove_label(self, item: Item, label: str) -> None:
        await self._gh_run(["issue", "edit", item.item_id, "--remove-label", label])

    async def update_body(self, item: Item, body: str, *, title: str | None = None) -> None:
        args = ["issue", "edit", item.item_id, "--body-file", "-"]
        if title is not None:
            args += ["--title", title]
        await self._gh_run(args, stdin=body.encode("utf-8"))

    async def ensure_label(
        self,
        name: str,
        *,
        color: str = "cccccc",
        description: str = "",
    ) -> None:
        await self._gh_run(
            [
                "label",
                "create",
                name,
                "--color",
                color,
                "--description",
                description,
                "--force",
            ]
        )

    async def _tracker_id_cached(self) -> str:
        if self._tracker_id is None:
            data = await self._gh_json(["repo", "view", "--json", "nameWithOwner"])
            self._tracker_id = f"github/{data['nameWithOwner']}"
        return self._tracker_id

    async def _gh_json(self, args: Sequence[str]) -> Any:
        proc = await asyncio.create_subprocess_exec(
            "gh",
            *args,
            cwd=self._project_dir,
            stdout=PIPE,
            stderr=PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise TrackerError(
                f"gh {' '.join(args)} failed (exit {proc.returncode}): {stderr.decode()}"
            )
        try:
            return json.loads(stdout)
        except json.JSONDecodeError as e:
            raise TrackerError(f"gh {' '.join(args)} produced unparseable JSON: {e}") from e

    async def _gh_run(self, args: Sequence[str], *, stdin: bytes | None = None) -> None:
        proc = await asyncio.create_subprocess_exec(
            "gh",
            *args,
            cwd=self._project_dir,
            stdin=PIPE if stdin is not None else None,
            stdout=PIPE,
            stderr=PIPE,
        )
        _, stderr = await proc.communicate(stdin)
        if proc.returncode != 0:
            raise TrackerError(
                f"gh {' '.join(args)} failed (exit {proc.returncode}): {stderr.decode()}"
            )

    def _to_item(self, record: Mapping[str, Any], tracker_id: str, *, with_comments: bool) -> Item:
        comments: tuple[Comment, ...] = ()
        if with_comments:
            comments = tuple(
                Comment(
                    author=c["author"]["login"],
                    body=c["body"],
                    created_at=datetime.fromisoformat(c["createdAt"].replace("Z", "+00:00")),
                )
                for c in record.get("comments", [])
            )
        return Item(
            tracker_id=tracker_id,
            item_id=str(record["number"]),
            title=record["title"],
            body=record["body"] or "",
            labels=tuple(lbl["name"] for lbl in record.get("labels", [])),
            comments=comments,
            created_at=datetime.fromisoformat(record["createdAt"].replace("Z", "+00:00")),
            updated_at=datetime.fromisoformat(record["updatedAt"].replace("Z", "+00:00")),
            url=record["url"],
        )
