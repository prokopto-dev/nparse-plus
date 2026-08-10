"""Shared test helpers.

The import-poison fixture below backs every "this layer must not import X"
guard in the suite: the Qt-free architecture rule (tests/test_architecture.py),
the plugins-off-means-nothing-imported rule (tests/core/plugins/
test_master_toggle.py), and the plugin template's standalone-mode check
(tests/core/plugins/test_template.py).

The two guards at the bottom are the other kind: no test may open a real
connection to a plugin registry, and no test may hand the next one a live Qt
worker thread.
"""

from __future__ import annotations

import subprocess
import sys
import threading
from collections.abc import Mapping, Sequence
from types import SimpleNamespace

import pytest

# A meta-path finder that refuses the named modules and their submodules.
#
# It MUST implement find_spec. The old find_module/load_module protocol was
# removed from CPython's import system in 3.12, so a finder providing only
# those is silently skipped and the "poison" lets everything through.
_PRELUDE = """
import sys
from importlib.abc import MetaPathFinder

_FORBIDDEN = {forbidden!r}


class _Poison(MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        for name in _FORBIDDEN:
            if fullname == name or fullname.startswith(name + "."):
                raise ImportError(f"import of {{fullname!r}} is forbidden here")
        return None


sys.meta_path.insert(0, _Poison())
"""


def poison_prelude(forbidden: Sequence[str]) -> str:
    """Python source that makes importing ``forbidden`` (or submodules) raise."""
    return _PRELUDE.format(forbidden=list(forbidden))


def run_poisoned(
    forbidden: Sequence[str],
    body: str,
    *,
    env: Mapping[str, str] | None = None,
    timeout: float = 120,
) -> subprocess.CompletedProcess[str]:
    """Run ``body`` in a subprocess where ``forbidden`` cannot be imported."""
    return subprocess.run(
        [sys.executable, "-c", poison_prelude(forbidden) + body],
        capture_output=True,
        text=True,
        timeout=timeout,
        env=dict(env) if env is not None else None,
    )


@pytest.fixture
def poisoned_import():
    """`run_poisoned` as a fixture — importlib import-mode hides tests/conftest."""
    return run_poisoned


@pytest.fixture(scope="session", autouse=True)
def no_live_registry_fetches():
    """Refuse any plugin fetch that would actually leave the machine.

    The suite mocks registry traffic at two levels — a ``fetch=`` callable
    passed into ``fetch_index``, or an ``httpx.MockTransport`` handed to
    ``fetch_https_bytes`` — so a call arriving here with neither is one
    nobody meant to make. Before this guard, one did: a test that built the
    plugin UI armed the 12 s post-launch update check, whose QTimer fired
    after that test had finished and started a live fetch inside an
    unrelated one. Concurrent sockets against another test's Qt teardown
    segfaulted the whole run in CI, which reads as "semantic release is
    broken" rather than "a test reached the internet".

    Session-scoped, because the point is to cover the gaps *between* tests
    too: a leaked timer fires wherever it likes, and a function-scoped patch
    would have been unwound by then.

    The transport seam stays open on purpose. ``patch_default_transport``
    and friends wrap whatever this attribute holds and re-call it with a
    MockTransport, so they keep working — they land on the `transport is
    not None` path and get the real implementation, offline.
    """
    from nparseplus.core.plugins import install

    real = install.fetch_https_bytes

    def guarded(url: str, *, transport=None, **kwargs):
        if transport is None:
            raise AssertionError(
                f"the test suite must not fetch {url!r} for real — pass a "
                "transport=httpx.MockTransport(...), inject fetch=, or turn "
                "the caller off (e.g. settings.plugins.update_check = False)"
            )
        return real(url, transport=transport, **kwargs)

    install.fetch_https_bytes = guarded
    try:
        yield
    finally:
        install.fetch_https_bytes = real


# --- leaked Qt worker threads -------------------------------------------------
#
# The thread half of the same bug as no_live_registry_fetches above, which
# fixed the network half and predicted this one (#61, then #63).
#
# ui/ runs its slow work on bare daemon threads that emit a Qt signal back into
# the window that started them, and joins none of them. A test drives one and
# waits on the signal with a timeout; when the wait expires on a loaded runner
# the test fails, but the thread lives on holding the page. qtbot then deletes
# the page, the worker emits into the corpse, and the run dies of
# SIGSEGV/SIGABRT wherever the next garbage collection happens — which is
# usually an unrelated test, so it reads as "CI is flaky" rather than "one test
# leaked a thread". That randomly failed the macOS job on any PR.
#
# Joining at teardown closes it, and costs a green run nothing: a worker only
# outlives its test when that test already failed.
#
# The other cause behind the same crash was not a leak at all — a waitSignal
# blocker torn down while a second worker was still to emit into it. That one
# is fixed where it lives, in tests/ui/test_pluginmanager.py.

#: Every thread name ui/ hands to threading.Thread. The plugin manager's three
#: are the ones a test drives today; the settings window's two have the same
#: shape (bare daemon, emits into the window that started it) and would fail
#: the same way. Asserted against the source by tests/ui/test_workerthreads.py,
#: so a sixth worker cannot be added without this set noticing.
UI_WORKER_THREAD_NAMES = frozenset(
    {
        "plugin-install",
        "plugin-update-check",
        "registry-fetch",
        "settings-update-check",
        "discord-login",
    }
)

#: How long a worker gets to finish before we call it hung. Only ever waited
#: on when a test already leaked one, so a generous value costs a healthy run
#: nothing.
UI_WORKER_JOIN_TIMEOUT = 30.0

_joined_ui_workers: list[str] = []
_stuck_ui_workers: list[str] = []


def join_ui_worker_threads(timeout: float = UI_WORKER_JOIN_TIMEOUT) -> list[str]:
    """Join every live UI worker thread; return the names that would not end.

    Records what it joined in `_joined_ui_workers` so a test can assert the
    guard actually fired rather than trusting that it did.
    """
    joined: list[str] = []
    stuck: list[str] = []
    for thread in threading.enumerate():
        if thread.name not in UI_WORKER_THREAD_NAMES or not thread.is_alive():
            continue
        thread.join(timeout)
        (stuck if thread.is_alive() else joined).append(thread.name)
    _joined_ui_workers[:] = joined
    return stuck


def _drop_queued_slot_calls() -> None:
    """Throw away what a joined worker posted on its way out.

    Joining is only half of it. The worker's last act is a cross-thread
    ``emit``, which Qt delivers as a *queued* metacall — and pytest-qt pumps
    the event loop from a teardown HOOKWRAPPER, i.e. after fixture
    finalization has already deleted the widgets and unwound the test's
    monkeypatches. So the handler runs against a torn-down world: it reaches
    for a deleted page, or pops the real modal QMessageBox the test had
    patched out. The result is moot once the test is over, so drop it.

    Scoped to MetaCall deliberately: DeferredDelete sits in the same queue and
    dropping that would leak every widget qtbot is about to free.
    """
    from PySide6.QtCore import QCoreApplication, QEvent

    if QCoreApplication.instance() is not None:
        QCoreApplication.removePostedEvents(None, QEvent.Type.MetaCall)


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_teardown(item: pytest.Item) -> None:
    """Join leaked workers BEFORE anything tears down the widgets they hold.

    tryfirst is load-bearing: the default implementation of this hook is what
    finalizes fixtures, and qtbot's finalizer is what deletes the page the
    worker is about to emit into. Joining after that point would be joining
    after the crash.

    Both halves are skipped entirely unless a worker was actually alive, so a
    green run pays one `threading.enumerate()` and never imports Qt here.

    This only records a hung worker; `_no_leaked_ui_workers` below is what
    fails the test. Raising here would skip the rest of the hook chain and so
    skip fixture teardown entirely, which is a worse outcome than the leak.
    """
    _stuck_ui_workers[:] = join_ui_worker_threads()
    if _joined_ui_workers:
        _drop_queued_slot_calls()


@pytest.fixture(autouse=True)
def _no_leaked_ui_workers():
    """Fail the test whose worker would not join, rather than let it segfault.

    Autouse and function-scoped, so its finalizer runs last — by which point
    the hook above has already done the joining. All this adds is a legible
    name for a run that is otherwise about to die somewhere else.
    """
    yield
    if _stuck_ui_workers:
        names = ", ".join(sorted(set(_stuck_ui_workers)))
        _stuck_ui_workers.clear()
        pytest.fail(
            f"this test left a live Qt worker thread ({names}) that would not "
            f"finish within {UI_WORKER_JOIN_TIMEOUT:g}s — it will emit into the "
            "next test's deleted widgets and crash the run"
        )


@pytest.fixture
def ui_worker_threads():
    """The guard above, for the tests that check it.

    A fixture because `--import-mode=importlib` hides tests/conftest from test
    modules — same reason `poisoned_import` exists.
    """
    return SimpleNamespace(
        names=UI_WORKER_THREAD_NAMES,
        join=join_ui_worker_threads,
        last_joined=lambda: list(_joined_ui_workers),
    )
