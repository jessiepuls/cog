from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Literal

from cog.core.item import Item


@dataclass(frozen=True)
class PullRequest:
    number: int
    url: str
    state: str  # "open" | "closed" | "merged"
    body: str
    head_branch: str


@dataclass(frozen=True)
class CheckRun:
    name: str
    state: Literal["pending", "passed", "failed", "skipped"]
    link: str
    description: str = ""


@dataclass(frozen=True)
class PrChecks:
    runs: tuple[CheckRun, ...]

    @property
    def pending(self) -> bool:
        return any(r.state == "pending" for r in self.runs)

    @property
    def failed(self) -> tuple[CheckRun, ...]:
        return tuple(r for r in self.runs if r.state == "failed")

    @property
    def all_passed(self) -> bool:
        return not self.pending and not self.failed


class GitHost(ABC):
    @abstractmethod
    async def push_branch(self, branch: str) -> None: ...

    @abstractmethod
    async def create_pr(self, *, head: str, title: str, body: str) -> PullRequest: ...

    @abstractmethod
    async def update_pr(self, number: int, *, body: str) -> None: ...

    @abstractmethod
    async def get_pr_for_branch(self, branch: str) -> PullRequest | None: ...

    @abstractmethod
    async def get_pr_body(self, number: int) -> str: ...

    @abstractmethod
    async def get_open_prs_mentioning_item(self, item: Item) -> list[PullRequest]: ...

    @abstractmethod
    async def get_pr_checks(self, number: int) -> PrChecks: ...

    @abstractmethod
    async def comment_on_pr(self, number: int, body: str) -> None: ...
