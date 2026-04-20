"""ChatView — freeform multi-turn chat with Claude (#130).

A fourth shell view (Ctrl+4). Unlike refine/ralph, there's no workflow —
just an open chat where the user can ask questions about the project,
explore the code, or draft text. Uses the standard
ClaudeCliRunner + DockerSandbox stack so env isolation (AWS / TF /
cloud creds) is preserved. Each turn rebuilds the prompt from the
running transcript (same pattern as refine's interview loop).
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.widget import Widget
from textual.worker import Worker

from cog.core.runner import ResultEvent, StatusEvent
from cog.state_paths import project_slug, project_state_dir
from cog.telemetry import StageTelemetry, TelemetryRecord, TelemetryWriter
from cog.ui.widgets.chat_pane import ChatPaneWidget

_DEFAULT_MODEL = "claude-opus-4-7"
_TRANSCRIPT_PREVIEW_LIMIT = 1000  # chars per message when rebuilding prompt


@dataclass(frozen=True)
class _Turn:
    role: str  # "user" or "assistant"
    content: str


def _load_preamble() -> str:
    return files("cog.prompts.claude.chat").joinpath("preamble.md").read_text(encoding="utf-8")


class ChatView(Widget):
    """Freeform Claude chat, mounted as the shell's Ctrl+4 view."""

    BINDINGS = [
        Binding("ctrl+k", "clear_chat", "Clear chat", show=False),
    ]

    DEFAULT_CSS = """
    ChatView {
        layout: vertical;
        height: 1fr;
    }
    """

    def __init__(self, project_dir: Path) -> None:
        super().__init__(id="view-chat")
        self._project_dir = project_dir
        self._transcript: list[_Turn] = []
        self._chat_pane: ChatPaneWidget | None = None
        self._loop_worker: Worker[None] | None = None

    def compose(self) -> ComposeResult:
        chat = ChatPaneWidget()
        self._chat_pane = chat
        yield chat

    async def on_mount(self) -> None:
        # Start the message loop — awaits user prompts, sends to Claude,
        # streams events back. Runs until the view is unmounted.
        self._loop_worker = self.run_worker(self._chat_loop(), exclusive=True)

    def focus_content(self) -> None:
        if self._chat_pane is None:
            return
        try:
            from textual.widgets import TextArea

            self._chat_pane.query_one("#input-area", TextArea).focus()
        except Exception:  # noqa: BLE001
            pass

    def needs_attention(self) -> str | None:
        # Chat is user-initiated — Claude only responds when prompted — so
        # there's no persistent attention state. Messages arrive, they're
        # rendered, that's it.
        return None

    def busy_description(self) -> str | None:
        # No reliable "busy" signal for chat — user can always exit the app.
        # Return None so the quit-confirm modal doesn't flag chat.
        return None

    async def _chat_loop(self) -> None:
        assert self._chat_pane is not None
        from cog.runners.claude_cli import ClaudeCliRunner
        from cog.runners.docker_sandbox import DockerSandbox

        sandbox = DockerSandbox()
        runner = ClaudeCliRunner(sandbox)
        model = os.environ.get("COG_CHAT_MODEL", _DEFAULT_MODEL)
        telemetry = TelemetryWriter(project_state_dir(self._project_dir))

        while True:
            user_message = await self._chat_pane.prompt()
            if user_message is None:
                # Escape / Ctrl+D — treat as reset. User can start a new turn.
                continue
            if not user_message.strip():
                continue
            self._transcript.append(_Turn(role="user", content=user_message))

            prompt = self._build_prompt()
            final_message = ""
            turn_cost = 0.0
            turn_start = time.monotonic()
            failed_error: str | None = None
            try:
                async for event in runner.stream(prompt, model=model):
                    if isinstance(event, ResultEvent):
                        final_message = event.result.final_message
                        turn_cost = event.result.total_cost_usd
                        continue
                    await self._chat_pane.emit(event)
            except Exception as e:  # noqa: BLE001 — surface any runner error in-line
                failed_error = f"{type(e).__name__}: {e}"
                await self._chat_pane.emit(StatusEvent(message=f"[red]chat error: {e}[/red]"))

            if final_message and not failed_error:
                self._transcript.append(_Turn(role="assistant", content=final_message))

            # One telemetry record per turn so chat shows up on the dashboard's
            # recent-runs strip. Ephemeral (no item, no branch, no PR).
            turn_duration = time.monotonic() - turn_start
            await self._write_turn_telemetry(
                telemetry,
                model=model,
                cost=turn_cost,
                duration=turn_duration,
                failed=failed_error,
            )

    async def _write_turn_telemetry(
        self,
        writer: TelemetryWriter,
        *,
        model: str,
        cost: float,
        duration: float,
        failed: str | None,
    ) -> None:
        from cog import __version__ as cog_version

        stage = StageTelemetry(
            stage="chat-turn",
            model=model,
            duration_s=duration,
            cost_usd=cost,
            exit_status=1 if failed else 0,
            commits=0,
        )
        record = TelemetryRecord(
            ts=datetime.now(UTC).isoformat(),
            cog_version=cog_version,
            project=project_slug(self._project_dir),
            workflow="chat",
            item=None,
            outcome="error" if failed else "success",
            branch=None,
            pr_url=None,
            duration_seconds=duration,
            stages=(stage,),
            total_cost_usd=cost,
            error=failed,
            cause_class=None,
            resumed=False,
        )
        await writer.write(record)

    def _build_prompt(self) -> str:
        parts = [_load_preamble(), "\n## Conversation\n"]
        for turn in self._transcript[:-1]:
            parts.append(f"\n### {turn.role.capitalize()}\n{turn.content}\n")
        # Latest user message is the prompt-tail.
        if self._transcript:
            last = self._transcript[-1]
            parts.append(f"\n### {last.role.capitalize()}\n{last.content}\n")
            parts.append("\n---\n\nRespond to the user's latest message.")
        return "\n".join(parts)

    def action_clear_chat(self) -> None:
        """Reset the conversation — transcript + visible log."""
        self._transcript = []
        if self._chat_pane is not None:
            try:
                log = self._chat_pane.query_one("#scrollback")
                log.clear()  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001
                pass
