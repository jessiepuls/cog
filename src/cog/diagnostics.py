"""Crash diagnostics: persistent log + exception capture.

Cog can disappear with no terminal output for several reasons — Textual's
worker-exception path renders tracebacks into an exit-renderable buffer
that's flushed *after* the alt-screen exits, and is lost if anything
interrupts the flush. This module captures those failures to disk
unconditionally so the next silent exit leaves a usable log.

Writes to `<state_dir>/cog.log` (typically
`~/.local/state/cog/<project-slug>/cog.log`).

What gets logged:

- `session start` / `session exit` markers (always).
- Unhandled sync exceptions via `sys.excepthook` (full traceback).
- Unhandled asyncio task exceptions via `loop.set_exception_handler`.
- Worker exceptions via a wrapper around `App._handle_exception`. Without
  this wrapper, Textual's exception path stays inside its own renderable
  buffer and the actual exception text is invisible to us.
- `SIGHUP` / `SIGTERM` via `loop.add_signal_handler` — surfaces external
  kill signals that Textual otherwise swallows quietly.

Three entry points wired up by callers:
- `setup_diagnostics(project_dir)`: configures the file logger and
  installs `sys.excepthook` + `atexit`. Call once at CLI entry.
- `install_asyncio_handler()`: installs the asyncio exception handler
  and signal handlers. Call from inside an async function that already
  has a running loop.
- `patch_handle_exception(app)`: wraps the App's `_handle_exception` to
  capture worker tracebacks before Textual buries them. Call after
  constructing the App but before `await app.run_async()`.
"""

from __future__ import annotations

import asyncio
import atexit
import io
import logging
import os
import signal
import sys
from pathlib import Path
from types import TracebackType

from cog.state_paths import project_state_dir

_INSTALLED = False


def setup_diagnostics(project_dir: Path) -> Path:
    """Configure the file logger and install `sys.excepthook` + `atexit`.

    Idempotent — safe to call from multiple entry points. Returns the log
    file path so callers can surface it.
    """
    global _INSTALLED
    state_dir = project_state_dir(project_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    log_path = state_dir / "cog.log"

    if _INSTALLED:
        return log_path

    handler = logging.FileHandler(log_path, mode="a", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    logger = logging.getLogger("cog.diagnostics")
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    logger.propagate = False

    pid = os.getpid()
    logger.info(f"=== session start (pid={pid}, argv={sys.argv}) ===")

    def excepthook(
        exc_type: type[BaseException],
        exc_value: BaseException,
        exc_tb: TracebackType | None,
    ) -> None:
        logger.error("unhandled exception", exc_info=(exc_type, exc_value, exc_tb))
        for h in logger.handlers:
            h.flush()
        sys.__excepthook__(exc_type, exc_value, exc_tb)

    sys.excepthook = excepthook

    def log_exit() -> None:
        logger.info(f"=== session exit (pid={pid}) ===")
        for h in logger.handlers:
            h.flush()

    atexit.register(log_exit)

    _INSTALLED = True
    return log_path


def install_asyncio_handler() -> None:
    """Install asyncio exception handler + SIGHUP/SIGTERM listeners.

    Must be called from inside an async function (a running loop must exist).
    """
    logger = logging.getLogger("cog.diagnostics")
    loop = asyncio.get_running_loop()

    def exc_handler(_loop: asyncio.AbstractEventLoop, context: dict[str, object]) -> None:
        msg = context.get("message", "<no message>")
        exc = context.get("exception")
        if isinstance(exc, BaseException):
            logger.error(f"asyncio task exception: {msg}", exc_info=exc)
        else:
            logger.error(f"asyncio task error: {msg} (context keys: {list(context)})")
        for h in logger.handlers:
            h.flush()

    loop.set_exception_handler(exc_handler)

    def signal_handler(sig: int) -> None:
        try:
            sig_name = signal.Signals(sig).name
        except (ValueError, AttributeError):
            sig_name = str(sig)
        logger.warning(f"received signal {sig_name} ({sig}); allowing default exit")
        for h in logger.handlers:
            h.flush()
        loop.stop()

    for sig in (signal.SIGHUP, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, signal_handler, sig)
        except (NotImplementedError, RuntimeError):
            # NotImplementedError on Windows; RuntimeError if loop isn't main-thread.
            pass


def patch_handle_exception(app: object) -> None:
    """Wrap `App._handle_exception` to log worker exceptions to disk.

    Textual's `_handle_exception` is called when a worker task raises. By
    default the traceback gets rendered into `_exit_renderables` — a
    buffer flushed *after* the alt-screen exits, easily lost. Wrapping it
    here captures the full traceback to `cog.log` synchronously, before
    Textual's panic path runs.

    This is the load-bearing piece: without it, silent crashes don't tell
    us what raised.
    """
    logger = logging.getLogger("cog.diagnostics")

    if not hasattr(app, "_handle_exception"):
        return
    original = app._handle_exception
    if not callable(original):
        return

    def wrapper(*args: object, **kwargs: object) -> object:
        # Render rich Traceback / renderable args to text so the log shows
        # the actual exception, not just `<Traceback object>`.
        rendered_args: list[str] = []
        for arg in args:
            try:
                from rich.console import Console as _RichConsole

                buf = io.StringIO()
                _RichConsole(file=buf, force_terminal=False, width=120).print(arg)
                rendered_args.append(buf.getvalue())
            except Exception:  # noqa: BLE001
                rendered_args.append(repr(arg))
        logger.error(
            f"App._handle_exception() called with kwargs={kwargs!r}\n"
            f"args (rendered):\n{chr(10).join(rendered_args)}"
        )
        for h in logger.handlers:
            h.flush()
        return original(*args, **kwargs)

    try:
        app._handle_exception = wrapper
    except (AttributeError, TypeError):
        pass
