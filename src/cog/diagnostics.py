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


async def run_app_traced(app: object) -> object:
    """Run `app.run_async()` with diagnostic logging on every return path.

    Captures exception types, app.return_value, and tracebacks for any path
    out of run_async — including paths that don't go through app.exit() (e.g.,
    KeyboardInterrupt, asyncio CancelledError, app._exit set directly).
    """
    import logging as _logging
    import traceback as _tb

    logger = _logging.getLogger("cog.diagnostics")
    logger.info("entering app.run_async()")
    for h in logger.handlers:
        h.flush()
    try:
        result = await app.run_async()  # type: ignore[attr-defined]
    except BaseException as exc:
        logger.warning(
            f"app.run_async() raised {type(exc).__name__}: {exc!r}"
            f"\nreturn_value={getattr(app, 'return_value', '<unset>')!r}"
            f"\ntraceback:\n{_tb.format_exc()}"
        )
        for h in logger.handlers:
            h.flush()
        raise
    logger.warning(
        f"app.run_async() returned normally."
        f" result={result!r}"
        f" return_value={getattr(app, 'return_value', '<unset>')!r}"
        f" _exit={getattr(app, '_exit', '<unset>')!r}"
    )
    for h in logger.handlers:
        h.flush()
    return result


def patch_app_exit(app: object) -> None:
    """Monkey-patch app methods + the message queue to log every shutdown path.

    Textual's message dispatcher uses class-dict lookup (message_pump.py:742),
    bypassing instance-level attributes — so we patch the *class*, not the
    instance, for the dispatched handlers. Plus we wrap _message_queue.put_nowait
    directly: every clean Textual shutdown puts None on that queue, so a wrapper
    there catches every path regardless of which method led to the put.
    """
    logger = logging.getLogger("cog.diagnostics")

    # Wrap the message queue's put_nowait. Putting None is the universal
    # signal for "close the pump"; everything else is a path that ends here.
    queue = getattr(app, "_message_queue", None)
    if queue is not None and hasattr(queue, "put_nowait"):
        original_put = queue.put_nowait

        def _put_nowait_wrapper(item: object) -> object:
            if item is None:
                import traceback as _tb

                logger.warning(
                    f"app._message_queue.put_nowait(None) — pump shutdown trigger"
                    f"\nstack:\n{''.join(_tb.format_stack())}"
                )
                for h in logger.handlers:
                    h.flush()
            return original_put(item)

        queue.put_nowait = _put_nowait_wrapper

    # Patch class-level methods (for dispatched handlers like _on_exit_app)
    # and instance-level methods (for direct calls like panic).
    app_cls = type(app)

    # All paths to a clean shutdown ultimately call _close_messages or
    # _close_messages_no_wait. Wrap those directly to catch any path,
    # including paths we haven't traced.
    def _make_wrapper(method_name: str, original_method: object) -> object:
        def wrapper(*args: object, **kwargs: object) -> object:
            import io as _io
            import traceback as _tb

            stack = "".join(_tb.format_stack())
            rendered_args: list[str] = []
            for arg in args:
                try:
                    from rich.console import Console as _RichConsole

                    _buf = _io.StringIO()
                    _RichConsole(file=_buf, force_terminal=False, width=120).print(arg)
                    rendered_args.append(_buf.getvalue())
                except Exception:  # noqa: BLE001
                    rendered_args.append(repr(arg))
            logger.warning(
                f"app.{method_name}() called with kwargs={kwargs!r}"
                f"\nargs (rendered):\n{chr(10).join(rendered_args)}"
                f"\ntrigger stack:\n{stack}"
            )
            for h in logger.handlers:
                h.flush()
            return original_method(*args, **kwargs)  # type: ignore[operator]

        return wrapper

    # Class-level patch: catches dispatched handlers (_on_exit_app, etc.)
    # whose lookup goes through cls.__dict__ and bypasses instance attrs.
    for attr in ("_on_exit_app", "_on_close_messages"):
        original = app_cls.__dict__.get(attr)
        if original is None or not callable(original):
            continue
        try:
            setattr(app_cls, attr, _make_wrapper(attr, original.__get__(app, app_cls)))
        except (AttributeError, TypeError):
            pass

    # Instance-level patch: catches direct self.method() calls
    # (panic, _close_messages, etc.) where instance dict wins.
    for attr in (
        "exit",
        "_handle_exception",
        "panic",
        "_fatal_error",
        "_close_messages",
        "_close_messages_no_wait",
    ):
        if not hasattr(app, attr):
            continue
        original = getattr(app, attr)
        if not callable(original):
            continue
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
