from collections.abc import Sequence
from typing import TYPE_CHECKING, Protocol

from cog.core.runner import RunEvent

if TYPE_CHECKING:
    from cog.core.item import Item


class RunEventSink(Protocol):
    """Consumes RunEvents during stage execution."""

    async def emit(self, event: RunEvent) -> None: ...


class UserInputProvider(Protocol):
    """Solicits a line of text from the user (interactive workflows only)."""

    async def prompt(self) -> str: ...


class ItemPicker(Protocol):
    """Solicits an Item selection from the user (Textual workflows only)."""

    async def pick(self, items: Sequence["Item"]) -> "Item | None":
        """Block until user picks one (returns Item) or cancels (returns None)."""
        ...
