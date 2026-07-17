"""crashguard — unhandled exceptions land in the crash log, app survives."""

from __future__ import annotations

import sys
import threading
from pathlib import Path

import pytest

from nparseplus import crashguard


@pytest.fixture
def hooks_restored():
    original = (sys.excepthook, threading.excepthook)
    yield
    sys.excepthook, threading.excepthook = original


def _boom() -> BaseException:
    try:
        raise ValueError("boom")
    except ValueError as exc:
        return exc


def test_excepthook_writes_traceback(tmp_path: Path, hooks_restored, capsys) -> None:
    log = tmp_path / "crash.log"
    crashguard.install(log)
    exc = _boom()

    sys.excepthook(type(exc), exc, exc.__traceback__)

    text = log.read_text()
    assert "ValueError: boom" in text
    assert "Traceback" in text
    assert "ValueError: boom" in capsys.readouterr().err


def test_threading_excepthook_logs_worker_crashes(tmp_path: Path, hooks_restored) -> None:
    log = tmp_path / "crash.log"
    crashguard.install(log)

    thread = threading.Thread(target=lambda: 1 / 0, name="worker")
    thread.start()
    thread.join()

    text = log.read_text()
    assert "ZeroDivisionError" in text
    assert "thread worker" in text


def test_log_exception_appends_with_context(tmp_path: Path) -> None:
    log = tmp_path / "crash.log"
    crashguard.log_exception(_boom(), log, context="event loop")
    text = log.read_text()
    assert "(event loop)" in text
    assert "ValueError: boom" in text


def test_rotation_past_size_cap(tmp_path: Path) -> None:
    log = tmp_path / "crash.log"
    log.write_text("x" * (crashguard.MAX_LOG_BYTES + 1))
    crashguard.log_exception(_boom(), log)
    assert log.with_name("crash.log.1").exists()
    assert "ValueError: boom" in log.read_text()


def test_keyboard_interrupt_falls_through(tmp_path: Path, hooks_restored) -> None:
    log = tmp_path / "crash.log"
    seen: list[type] = []
    sys.excepthook = lambda exc_type, exc, tb: seen.append(exc_type)
    crashguard.install(log)

    sys.excepthook(KeyboardInterrupt, KeyboardInterrupt(), None)

    assert seen == [KeyboardInterrupt]
    assert not log.exists()
