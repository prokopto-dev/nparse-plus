"""Last-resort exception logging: unhandled errors land in a crash log.

PySide6 routes exceptions that escape Qt slots through ``sys.excepthook``;
in the frozen app that traceback went to a console nobody can see (and on
PySide6 builds configured to abort on unhandled errors, took the process
down with it). ``install()`` hooks ``sys.excepthook`` and
``threading.excepthook`` to append the traceback to a crash log (and
stderr), so "the whole application crashed" reports come with evidence.

Deliberately stdlib-only and Qt-free: it must be importable and installed
before Qt ever loads, and it must never raise (a failing crash handler is
worse than none). No dialogs from the hooks — Qt may be mid-teardown.
"""

from __future__ import annotations

import contextlib
import sys
import threading
import traceback
from datetime import datetime
from pathlib import Path

MAX_LOG_BYTES = 1_000_000  # rotate to <name>.1 past this


def _format(exc_type, exc, tb, context: str = "") -> str:
    header = f"[{datetime.now().isoformat(timespec='seconds')}]"
    if context:
        header += f" ({context})"
    body = "".join(traceback.format_exception(exc_type, exc, tb))
    return f"{header}\n{body}\n"


def _append(log_file: Path, text: str) -> None:
    try:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        if log_file.exists() and log_file.stat().st_size > MAX_LOG_BYTES:
            log_file.replace(log_file.with_name(log_file.name + ".1"))
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(text)
    except Exception:
        pass  # best-effort only: never raise from a crash handler


def log_exception(exc: BaseException, log_file: Path, context: str = "") -> None:
    """Log an already-caught exception with the standard crash format."""
    text = _format(type(exc), exc, exc.__traceback__, context)
    with contextlib.suppress(Exception):
        sys.stderr.write(text)
    _append(log_file, text)


def install(log_file: Path) -> None:
    """Install process-wide hooks that log unhandled exceptions to ``log_file``.

    KeyboardInterrupt keeps the previous behavior (default hook) so Ctrl-C
    still exits cleanly.
    """
    previous_excepthook = sys.excepthook

    def _excepthook(exc_type, exc, tb):
        if issubclass(exc_type, KeyboardInterrupt):
            previous_excepthook(exc_type, exc, tb)
            return
        text = _format(exc_type, exc, tb)
        with contextlib.suppress(Exception):
            sys.stderr.write(text)
        _append(log_file, text)

    def _threading_excepthook(args):
        if args.exc_type is SystemExit:
            return
        thread_name = args.thread.name if args.thread is not None else "?"
        text = _format(args.exc_type, args.exc_value, args.exc_traceback, f"thread {thread_name}")
        with contextlib.suppress(Exception):
            sys.stderr.write(text)
        _append(log_file, text)

    sys.excepthook = _excepthook
    threading.excepthook = _threading_excepthook
