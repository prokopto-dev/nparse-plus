"""The teardown guard that joins leaked Qt worker threads.

The guard itself lives in tests/conftest.py (reached here through the
`ui_worker_threads` fixture, since `--import-mode=importlib` hides conftest
from test modules). These are its unit tests plus the coverage check that
keeps its name list honest.

Why it exists: ui/ starts bare daemon threads that emit a Qt signal back into
the widget that started them, and joins none of them. A test that waits on
that signal with a timeout — every install test in test_pluginmanager.py —
leaves the worker running when the wait expires on a loaded runner. qtbot then
deletes the widget, the worker emits into it, and the NEXT test dies of
SIGSEGV. The end-to-end regression test for that lives with the tests that
cause it, at the bottom of test_pluginmanager.py.
"""

from __future__ import annotations

import ast
import threading
from pathlib import Path

UI_DIR = Path(__file__).resolve().parents[2] / "src" / "nparseplus" / "ui"


def thread_names_in(source: str) -> set[str]:
    """Every literal ``name=`` handed to a ``threading.Thread(...)`` call."""
    names: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        target = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        if target != "Thread":
            continue
        for kw in node.keywords:
            if kw.arg == "name" and isinstance(kw.value, ast.Constant):
                names.add(kw.value.value)
    return names


def test_the_guard_names_every_worker_thread_the_ui_starts(ui_worker_threads) -> None:
    """A new bare daemon thread in ui/ must join the guard's list.

    Unnamed to the guard means unjoined at teardown, which is the crash this
    whole file exists to prevent.
    """
    started: dict[str, set[str]] = {}
    for path in sorted(UI_DIR.rglob("*.py")):
        found = thread_names_in(path.read_text(encoding="utf-8"))
        if found:
            started[path.name] = found

    assert started, "expected to find named worker threads in ui/ — did the AST scan break?"
    missing = set().union(*started.values()) - ui_worker_threads.names
    assert not missing, (
        f"ui/ starts worker thread(s) {sorted(missing)} that the teardown guard in "
        f"tests/conftest.py does not join — add them to UI_WORKER_THREAD_NAMES "
        f"(found: {started})"
    )


def test_join_waits_for_a_worker_that_is_still_finishing(ui_worker_threads) -> None:
    """The ordinary case: the CI wait expired, the work itself is fine."""
    done = threading.Event()
    worker = threading.Thread(target=lambda: done.wait(5), name="plugin-install", daemon=True)
    worker.start()
    done.set()

    assert ui_worker_threads.join(timeout=5.0) == []
    assert not worker.is_alive()
    assert ui_worker_threads.last_joined() == ["plugin-install"]


def test_join_reports_a_worker_that_will_not_finish(ui_worker_threads) -> None:
    """A hung worker is named, not silently waited on forever."""
    release = threading.Event()
    worker = threading.Thread(target=lambda: release.wait(30), name="registry-fetch", daemon=True)
    worker.start()
    try:
        assert ui_worker_threads.join(timeout=0.05) == ["registry-fetch"]
        assert ui_worker_threads.last_joined() == []
    finally:
        release.set()
        worker.join(5)
    assert not worker.is_alive()


def test_join_ignores_threads_the_guard_does_not_own(ui_worker_threads) -> None:
    release = threading.Event()
    worker = threading.Thread(target=lambda: release.wait(30), name="some-app-thread", daemon=True)
    worker.start()
    try:
        assert ui_worker_threads.join(timeout=0.05) == []
        assert worker.is_alive()
    finally:
        release.set()
        worker.join(5)
