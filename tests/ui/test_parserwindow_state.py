"""ParserWindow.apply_window_state — unconditional flag re-apply + re-show.

Regression cover for the Linux report where the Maps window ignored the
settings window's "On top" toggle: legacy windows now get the same direct
apply_window_state() call the new overlay windows get, and the typed
toggle_clickthrough_<name> command must not leave the window hidden
(setWindowFlags() hides; only a re-show brings it back).
"""

from __future__ import annotations

from datetime import datetime

import pytest
from PySide6.QtCore import Qt

# helpers.application is imported at module scope on purpose: it runs
# config.load("nparse.config.json") + verify_settings() at import time, and
# the fixture below must re-point the config at tmp_path AFTER that.
from nparseplus.helpers import config
from nparseplus.helpers.application import NomnsParse
from nparseplus.helpers.parser import ParserWindow
from nparseplus.helpers.settings import SettingsSignals

pytestmark = pytest.mark.qt

NOW = datetime(2026, 7, 15, 12, 0, 0)


@pytest.fixture
def window(qapp, qtbot, tmp_path, monkeypatch) -> ParserWindow:
    config.load(str(tmp_path / "nparse.config.json"))
    config.verify_settings()
    config.data["testwin"] = {
        "geometry": [0, 0, 200, 200],
        "toggled": False,
        "always_on_top": True,
        "clickthrough": False,
        "frameless": True,
        "auto_hide_menu": True,
        "opacity": 80,
    }
    monkeypatch.setattr(config, "save", lambda: None)
    signals = getattr(qapp, "_signals", None)
    if signals is None:
        signals = {}
        qapp._signals = signals
    signals["settings"] = SettingsSignals()
    win = ParserWindow(name="testwin")
    qtbot.addWidget(win)
    return win


def test_apply_window_state_reapplies_flags_and_reshows(window: ParserWindow) -> None:
    window.show()
    config.data["testwin"]["always_on_top"] = False
    config.data["testwin"]["clickthrough"] = True
    config.data["testwin"]["opacity"] = 50

    window.apply_window_state()

    assert window.isVisible()  # setWindowFlags() hid it; re-shown
    flags = window.windowFlags()
    assert not flags & Qt.WindowType.WindowStaysOnTopHint
    assert flags & Qt.WindowType.WindowTransparentForInput
    assert window.windowOpacity() == pytest.approx(0.5, abs=0.01)  # 8-bit quantized


def test_apply_window_state_keeps_hidden_window_hidden(window: ParserWindow) -> None:
    assert not window.isVisible()
    config.data["testwin"]["always_on_top"] = False

    window.apply_window_state()

    assert not window.isVisible()
    assert not window.windowFlags() & Qt.WindowType.WindowStaysOnTopHint


def test_toggle_clickthrough_command_keeps_window_visible(window: ParserWindow) -> None:
    # _parse only touches self._parsers, so drive the real method over a stub
    # app (constructing NomnsParse would build every legacy window).
    stub = type("StubApp", (), {"_parsers": [window]})()
    window.show()

    NomnsParse._parse(stub, (NOW, "toggle_clickthrough_testwin"))

    assert config.data["testwin"]["clickthrough"] is True
    assert window.isVisible()
    assert window.windowFlags() & Qt.WindowType.WindowTransparentForInput
