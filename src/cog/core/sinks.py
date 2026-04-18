from typing import Protocol

from cog.core.runner import RunEvent


class RunEventSink(Protocol):
    """Consumes RunEvents during stage execution."""

    async def emit(self, event: RunEvent) -> None: ...


class UserInputProvider(Protocol):
    """Solicits a line of text from the user (interactive workflows only)."""

    async def prompt(self) -> str: ...
