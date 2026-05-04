"""Crash diagnostics: persistent log + exception/signal hooks.

Set up by `cli.py` / `ui/wire.py` early in each cog invocation. Writes to
`<state_dir>/cog.log`. The aim is to capture *what killed cog* in cases
where it disappears with no terminal output (e.g., received a SIGHUP, or
a worker raised an exception that Textual swallowed during teardown).

Read the log after a crash with `tail ~/.local/state/cog/<slug>/cog.log`.
"""

from __future__ import annotations

import asyncio
import atexit
import logging
import os
import signal
import sys
from pathlib import Path
from types import TracebackType

from cog.state_paths import project_state_dir

_INSTALLED = False


def setup_diagnostics(project_dir: Path) -> Path:
    """Configure crash logging. Idempotent — safe to call from multiple entry points.

    Returns the log file path so callers can surface it on stderr if useful.
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
    logger.setLevel(logging.DEBUG)
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

    def signal_handler(signum: int, frame: object) -> None:
        try:
            sig_name = signal.Signals(signum).name
        except (ValueError, AttributeError):
            sig_name = str(signum)
        logger.warning(f"received signal {sig_name} ({signum}); exiting")
        for h in logger.handlers:
            h.flush()
        # Restore default handler and re-raise so the process exits normally.
        signal.signal(signum, signal.SIG_DFL)
        os.kill(pid, signum)

    # SIGHUP is the prime suspect for "cog disappears while idle in another tab."
    # SIGTERM is what other processes send to politely kill us.
    # Don't touch SIGINT — Textual handles Ctrl+C cleanly already.
    for sig in (signal.SIGHUP, signal.SIGTERM):
        try:
            signal.signal(sig, signal_handler)
        except (OSError, ValueError):
            pass

    def log_exit() -> None:
        # Dump the current stack so we can see who triggered the exit when no
        # signal / exception was logged. Python's atexit fires inside whatever
        # frame called sys.exit / completed run_async / etc.
        import traceback

        stack = "".join(traceback.format_stack())
        logger.info(f"=== session exit (pid={pid}) ===\nexit stack:\n{stack}")
        for h in logger.handlers:
            h.flush()

    atexit.register(log_exit)

    _INSTALLED = True
    return log_path


def patch_app_exit(app: object) -> None:
    """Monkey-patch app.exit / app._exit to log the call stack at trigger time.

    The atexit hook fires after Python shutdown begins, by which point the
    frame that triggered exit is no longer on the stack. Wrapping at the
    actual exit-call entry point captures who pulled the trigger.
    """
    logger = logging.getLogger("cog.diagnostics")

    for attr in ("exit", "_exit", "_handle_exception"):
        if not hasattr(app, attr):
            continue
        original = getattr(app, attr)
        if not callable(original):
            continue

        def _make_wrapper(method_name: str, original_method: object) -> object:
            def wrapper(*args: object, **kwargs: object) -> object:
                import traceback as _tb

                stack = "".join(_tb.format_stack())
                logger.warning(
                    f"app.{method_name}() called with args={args!r} kwargs={kwargs!r}"
                    f"\ntrigger stack:\n{stack}"
                )
                for h in logger.handlers:
                    h.flush()
                return original_method(*args, **kwargs)  # type: ignore[operator]

            return wrapper

        try:
            setattr(app, attr, _make_wrapper(attr, original))
        except (AttributeError, TypeError):
            pass


def install_asyncio_handler() -> None:
    """Install asyncio-level exception handler, signal handlers, and a liveness heartbeat.

    Must be called from inside an async function (a running loop must exist).

    - Exception handler catches unhandled task exceptions Textual would swallow.
    - asyncio signal handlers catch SIGHUP/SIGTERM that bypass `signal.signal()`
      because Textual / asyncio installs loop-level signal handlers that take
      precedence.
    - Heartbeat task logs every 60s so we can distinguish "process was alive
      up until the moment of death" from "process was stuck for N minutes
      before the exit log fired."
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

    def asyncio_signal_handler(sig: int) -> None:
        try:
            sig_name = signal.Signals(sig).name
        except (ValueError, AttributeError):
            sig_name = str(sig)
        logger.warning(f"asyncio received signal {sig_name} ({sig}); allowing default exit")
        for h in logger.handlers:
            h.flush()
        # Stop the loop so the app exits cleanly. Don't re-raise — that'd
        # potentially fight whatever Textual was doing.
        loop.stop()

    for sig in (signal.SIGHUP, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, asyncio_signal_handler, sig)
        except (NotImplementedError, RuntimeError):
            # NotImplementedError on Windows; RuntimeError if loop isn't main-thread
            pass

    async def heartbeat() -> None:
        while True:
            await asyncio.sleep(60)
            logger.debug("heartbeat")
            for h in logger.handlers:
                h.flush()

    # Keep a reference so the task isn't GC'd
    loop.create_task(heartbeat(), name="cog-diagnostics-heartbeat")
