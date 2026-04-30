import asyncio
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

from cog.core.errors import HostError
from cog.core.host import CheckRun, GitHost, PrChecks, PullRequest
from cog.core.item import Item

_NO_CHECKS_REPORTED_MARKER = "no checks reported"

_GH_STATE_MAP: dict[str, Literal["pending", "passed", "failed", "skipped"]] = {
    "SUCCESS": "passed",
    "FAILURE": "failed",
    "PENDING": "pending",
    "QUEUED": "pending",
    "IN_PROGRESS": "pending",
    "SKIPPED": "skipped",
}


def _map_gh_state(raw: str) -> Literal["pending", "passed", "failed", "skipped"]:
    return _GH_STATE_MAP.get(raw.upper(), "pending")


class GitHubGitHost(GitHost):
    def __init__(self, project_dir: Path) -> None:
        self._project_dir = project_dir

    async def push_branch(self, branch: str) -> None:
        await self._git_run(["push", "-u", "origin", branch])

    async def create_pr(self, *, head: str, title: str, body: str) -> PullRequest:
        stdout = await self._gh_stdout(
            ["pr", "create", "--head", head, "--title", title, "--body-file", "-"],
            stdin=body.encode("utf-8"),
        )
        url = self._parse_pr_url(stdout)
        number = int(url.rsplit("/", 1)[-1])
        return PullRequest(number=number, url=url, state="open", body=body, head_branch=head)

    async def update_pr(self, number: int, *, body: str) -> None:
        await self._gh_run(
            ["pr", "edit", str(number), "--body-file", "-"],
            stdin=body.encode("utf-8"),
        )

    async def get_pr_for_branch(self, branch: str) -> PullRequest | None:
        data = await self._gh_json(
            [
                "pr",
                "list",
                "--head",
                branch,
                "--state",
                "open",
                "--json",
                "number,url,state,body,headRefName",
            ]
        )
        if not data:
            return None
        # Rare: multiple open PRs for same head (gh orders newest-first); take first.
        return self._to_pr(data[0])

    async def get_pr_body(self, number: int) -> str:
        data = await self._gh_json(["pr", "view", str(number), "--json", "body"])
        return data["body"] or ""

    async def get_pr_checks(self, number: int) -> PrChecks:
        try:
            stdout = await self._gh_json(
                ["pr", "checks", str(number), "--json", "name,state,link,description"]
            )
        except HostError as exc:
            if _NO_CHECKS_REPORTED_MARKER in str(exc):
                return PrChecks(runs=())
            raise
        runs = tuple(
            CheckRun(
                name=r["name"],
                state=_map_gh_state(r["state"]),
                link=r.get("link", ""),
                description=r.get("description", ""),
            )
            for r in stdout
        )
        return PrChecks(runs=runs)

    async def comment_on_pr(self, number: int, body: str) -> None:
        await self._gh_run(
            ["pr", "comment", str(number), "--body-file", "-"],
            stdin=body.encode("utf-8"),
        )

    async def get_open_prs_mentioning_item(self, item: Item) -> list[PullRequest]:
        query = f'"Closes #{item.item_id}" OR "Fixes #{item.item_id}" OR "Resolves #{item.item_id}"'
        data = await self._gh_json(
            [
                "pr",
                "list",
                "--search",
                query,
                "--state",
                "open",
                "--json",
                "number,url,state,body,headRefName",
            ]
        )
        return [self._to_pr(record) for record in data]

    @staticmethod
    def _parse_pr_url(stdout: bytes) -> str:
        text = stdout.decode("utf-8").strip()
        if not text:
            raise HostError("gh pr create produced empty stdout")
        # gh sometimes prints progress before the URL; URL is always the last line.
        last = text.splitlines()[-1].strip()
        if not last.startswith("http") or "/pull/" not in last:
            raise HostError(f"gh pr create: unrecognized URL in stdout: {text!r}")
        return last

    @staticmethod
    def _to_pr(record: Mapping[str, Any]) -> PullRequest:
        return PullRequest(
            number=record["number"],
            url=record["url"],
            state=record["state"].lower(),  # gh returns "OPEN"/"CLOSED"/"MERGED"
            body=record["body"] or "",
            head_branch=record["headRefName"],
        )

    async def _gh_json(self, args: list[str]) -> Any:
        stdout = await self._gh_stdout(args)
        try:
            return json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise HostError(f"gh returned invalid JSON: {stdout!r}") from exc

    async def _gh_stdout(self, args: list[str], *, stdin: bytes | None = None) -> bytes:
        return await self._run(["gh", *args], stdin=stdin)

    async def _gh_run(self, args: list[str], *, stdin: bytes | None = None) -> None:
        await self._run(["gh", *args], stdin=stdin)

    async def _git_run(self, args: list[str]) -> None:
        await self._run(["git", *args])

    async def _run(self, argv: list[str], *, stdin: bytes | None = None) -> bytes:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE if stdin is not None else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self._project_dir,
        )
        stdout, stderr = await proc.communicate(stdin)
        if proc.returncode != 0:
            cmd = " ".join(argv)
            raise HostError(
                f"{cmd!r} exited {proc.returncode}: {stderr.decode('utf-8', errors='replace')}"
            )
        return stdout
