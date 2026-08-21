"""Session-restore tests: `shown` reflects the last DELIBERATE visibility
choice and app quit never mutates it downward (offscreen Qt via pytest-qt).

Covers the fix for the macOS Cmd+Q / Dock-quit path, where Qt closes every
window via ``closeAllWindows()`` before ``aboutToQuit`` fires — without the
shared quit flag each window would clobber its ``shown`` to False and the next
launch would open nothing.
"""

from __future__ import annotations

import types

import pytest

from nparseplus.config.settings import Settings, WindowState
from nparseplus.core.dps import FightTracker
from nparseplus.ui import appquit
from nparseplus.ui.consolewindow import WINDOW_KEY as CONSOLE_KEY
from nparseplus.ui.consolewindow import ConsoleWindow
from nparseplus.ui.dpswindow import WINDOW_KEY as DPS_KEY
from nparseplus.ui.dpswindow import DpsMeterWindow

pytestmark = pytest.mark.qt


@pytest.fixture(autouse=True)
def _reset_quit_flag():
    """The quit flag is a process global; keep tests independent of order."""
    appquit.reset()
    yield
    appquit.reset()


def _console(settings: Settings) -> ConsoleWindow:
    return ConsoleWindow(settings)


def _dps_backend() -> types.SimpleNamespace:
    return types.SimpleNamespace(settings=Settings(), fights=FightTracker())


def test_shown_true_restores_visible(qtbot):
    settings = Settings()
    settings.windows[CONSOLE_KEY] = WindowState(frameless=False, shown=True)
    window = _console(settings)
    qtbot.addWidget(window)
    assert window.isVisible()


def test_shown_false_stays_hidden(qtbot):
    settings = Settings()
    settings.windows[CONSOLE_KEY] = WindowState(frameless=False, shown=False)
    window = _console(settings)
    qtbot.addWidget(window)
    assert not window.isVisible()


def test_dps_shown_true_restores_visible(qtbot):
    backend = _dps_backend()
    backend.settings.windows[DPS_KEY] = WindowState(shown=True)
    window = DpsMeterWindow(backend)
    qtbot.addWidget(window)
    assert window.isVisible()


def test_close_while_quitting_keeps_shown_true(qtbot):
    settings = Settings()
    settings.windows[CONSOLE_KEY] = WindowState(frameless=False, shown=True)
    window = _console(settings)
    qtbot.addWidget(window)
    assert window.isVisible()

    appquit.mark_quitting()  # simulate the tray Quit / Cmd+Q path
    window.close()

    # Quit must not flip the durable choice downward.
    assert settings.windows[CONSOLE_KEY].shown is True


def test_user_close_persists_shown_false(qtbot):
    settings = Settings()
    settings.windows[CONSOLE_KEY] = WindowState(frameless=False, shown=True)
    window = _console(settings)
    qtbot.addWidget(window)
    assert window.isVisible()

    window.close()  # deliberate user close, not a quit

    assert settings.windows[CONSOLE_KEY].shown is False


def test_toggle_persists_shown_both_ways(qtbot):
    settings = Settings()
    settings.windows[CONSOLE_KEY] = WindowState(frameless=False, shown=False)
    window = _console(settings)
    qtbot.addWidget(window)
    assert not window.isVisible()

    window.toggle()
    assert window.isVisible()
    assert settings.windows[CONSOLE_KEY].shown is True

    window.toggle()
    assert not window.isVisible()
    assert settings.windows[CONSOLE_KEY].shown is False


def test_on_app_quit_preserves_durable_shown(qtbot):
    """aboutToQuit fires after windows were already closed (Cmd+Q): the
    window is not visible, but ``shown`` must keep its durable value."""
    settings = Settings()
    settings.windows[CONSOLE_KEY] = WindowState(frameless=False, shown=True)
    window = _console(settings)
    qtbot.addWidget(window)

    appquit.mark_quitting()
    window.hide()  # closeAllWindows already hid it before aboutToQuit
    window._on_app_quit()

    assert settings.windows[CONSOLE_KEY].shown is True
