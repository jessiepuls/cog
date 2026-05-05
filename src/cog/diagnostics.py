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


def patch_await_remove_logging() -> None:
    """Wrap AwaitRemove to log which widget removal raised CancelledError.

    AwaitRemove records the caller file/line where `.remove()` was called.
    When its gather sees a cancellation, log the caller and the list of
    tasks being gathered. This identifies WHICH widget is being removed
    when the cancellation cascade fires.

    Does NOT swallow the cancellation — it propagates up as before, so
    Textual's normal shutdown happens. We just learn the source.
    """
    import asyncio

    import textual.await_remove as _ar

    logger = logging.getLogger("cog.diagnostics")

    def patched_await(self: object) -> object:
        current_task = asyncio.current_task()
        tasks = [t for t in self._tasks if t is not current_task]  # type: ignore[attr-defined]
        caller = getattr(self, "_caller", "<unknown>")

        async def await_prune_with_logging() -> None:
            try:
                await asyncio.gather(*tasks)
            except BaseException as exc:
                # Log which AwaitRemove this was; then re-raise as Textual expects.
                task_info = [(t.get_name(), t.done(), t.cancelled()) for t in tasks]
                logger.error(
                    f"AwaitRemove gather raised {type(exc).__name__}: {exc!r}\n"
                    f"caller (widget.remove() site): {caller}\n"
                    f"tasks: {task_info}"
                )
                for h in logger.handlers:
                    h.flush()
                raise
            if self._post_remove is not None:  # type: ignore[attr-defined]
                from textual._callback import invoke as _invoke

                await _invoke(self._post_remove)  # type: ignore[attr-defined]

        return await_prune_with_logging().__await__()

    _ar.AwaitRemove.__await__ = patched_await  # type: ignore[method-assign,assignment]


def patch_message_loop() -> None:
    """Wrap `MessagePump._process_messages_loop` to log CancelledError exits.

    Cancellations propagating into the App's message-pump loop are silently
    swallowed by Textual's outer `except CancelledError: pass`. The app
    shuts down cleanly with no exception ever surfacing — `_handle_exception`
    isn't called, so `patch_handle_exception` doesn't catch it.

    Wrap the loop method itself so the cancellation+traceback lands in
    cog.log before the silent exit. Only traces the App's own pump (not
    every Widget) to keep noise down.

    Call once before `app.run_async()`.
    """
    import textual.message_pump as _mp

    logger = logging.getLogger("cog.diagnostics")
    original = _mp.MessagePump._process_messages_loop

    async def traced(self: _mp.MessagePump) -> None:
        if type(self).__name__ != "CogApp":
            await original(self)
            return
        try:
            await original(self)
        except BaseException as exc:
            import traceback as _tb

            logger.error(
                f"_process_messages_loop raised {type(exc).__name__}: {exc!r}"
                f"\ntraceback:\n{_tb.format_exc()}"
            )
            for h in logger.handlers:
                h.flush()
            raise

    _mp.MessagePump._process_messages_loop = traced  # type: ignore[method-assign]


async def run_app_traced(app: object) -> object:
    """Run `app.run_async()` and log how it returns.

    `run_async` can return normally without our other wrappers seeing
    anything when the message pump is closed via a path that doesn't
    raise (e.g. None put on the queue, app.exit, _shutdown). This
    captures `app._exit` / `app.return_value` / any propagating
    exception.
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
        logger.error(
            f"app.run_async() raised {type(exc).__name__}: {exc!r}\ntraceback:\n{_tb.format_exc()}"
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


def patch_pump_shutdown_paths(app: object) -> None:
    """Wrap every path that can close the message pump silently.

    The message-pump loop exits cleanly (no exception) when None is put
    on its queue. Several paths reach there:
    - `_close_messages` (line 484 puts None)
    - `_close_messages_no_wait` (posts CloseMessages → handler does the put)
    - `_on_exit_app` (puts None directly)
    - `panic` / `_fatal_error` (call _close_messages_no_wait)

    Wrap them all so we know which path actually closed the pump.
    Also wrap the queue's put_nowait directly as a backstop for any
    path we missed.
    """
    import traceback as _tb

    logger = logging.getLogger("cog.diagnostics")

    queue = getattr(app, "_message_queue", None)
    if queue is not None and hasattr(queue, "put_nowait"):
        original_put = queue.put_nowait

        def _put_nowait_wrapper(item: object) -> object:
            if item is None:
                logger.warning(
                    f"_message_queue.put_nowait(None) — pump shutdown"
                    f"\nstack:\n{''.join(_tb.format_stack())}"
                )
                for h in logger.handlers:
                    h.flush()
            return original_put(item)

        queue.put_nowait = _put_nowait_wrapper

    def _make_wrapper(method_name: str, original_method: object) -> object:
        def wrapper(*args: object, **kwargs: object) -> object:
            logger.warning(
                f"app.{method_name}() called — pump shutdown path"
                f"\nstack:\n{''.join(_tb.format_stack())}"
            )
            for h in logger.handlers:
                h.flush()
            return original_method(*args, **kwargs)  # type: ignore[operator]

        return wrapper

    for attr in ("exit", "panic", "_fatal_error", "_close_messages", "_close_messages_no_wait"):
        original = getattr(app, attr, None)
        if not callable(original):
            continue
        try:
            setattr(app, attr, _make_wrapper(attr, original))
        except (AttributeError, TypeError):
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
