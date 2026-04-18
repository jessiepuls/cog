from abc import ABC, abstractmethod
from typing import ClassVar

from cog.core.item import Item


class IssueTracker(ABC):
    can_read: ClassVar[bool]
    can_comment: ClassVar[bool]
    can_swap_labels: ClassVar[bool]
    can_create_linked: ClassVar[bool]

    @abstractmethod
    async def list_by_label(self, label: str, *, assignee: str | None = None) -> list[Item]: ...

    @abstractmethod
    async def get(self, item_id: str) -> Item: ...

    @abstractmethod
    async def comment(self, item: Item, body: str) -> None: ...

    @abstractmethod
    async def add_label(self, item: Item, label: str) -> None: ...

    @abstractmethod
    async def remove_label(self, item: Item, label: str) -> None: ...

    @abstractmethod
    async def update_body(self, item: Item, body: str, *, title: str | None = None) -> None: ...
