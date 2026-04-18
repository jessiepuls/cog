from abc import ABC, abstractmethod
from dataclasses import dataclass

from cog.core.item import Item


@dataclass(frozen=True)
class PullRequest:
    number: int
    url: str
    state: str  # "open" | "closed" | "merged"
    body: str
    head_branch: str


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
