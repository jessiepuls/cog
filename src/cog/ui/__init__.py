"""Textual TUI for cog workflows."""

from cog.core.workflow import Workflow

# Registry of workflow classes shown in the main menu.
# Populated by concrete workflow registrations as they land (#12, #18).
WORKFLOWS: list[type[Workflow]] = []
