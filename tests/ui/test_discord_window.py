"""The live Discord overlay window: the alpha channel it is created with.

Same construction recipe as the Maps window tests — the legacy
``QApplication._signals`` the real ``NomnsParse`` provides, plus a scratch
legacy config so no run touches the developer's own ``nparse.config.json``.

QtWebEngine stays out of it: ``_setup_webview`` already has a supported
no-QtWebEngine path (Linux systems where the import fails), and taking it
keeps this about the window rather than about Discord's page.
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication

from nparseplus.helpers import config
from nparseplus.helpers.settings import SettingsSignals
from nparseplus.parsers import discord as discord_module
from nparseplus.parsers.discord import Discord

pytestmark = pytest.mark.qt


# -- the overlay needs a window that can hold alpha (#101) ----------------------
#
# Same ordering bug as the map's backdrop (#99): WA_TranslucentBackground was
# set in Discord.__init__ AFTER super().__init__(), which shows the window
# whenever it was open at last quit. QWindow::setFormat() past create() does
# not recreate anything, so the request was never granted and the platform
# window had no alpha channel until a Settings Apply recreated it
# (apply_window_state -> _set_flags -> setWindowFlags).


@pytest.fixture
def discord_open(qtbot, tmp_path, monkeypatch):
    """A Discord window that was open at last quit — so ParserWindow.__init__
    shows it, which is when the platform window gets created."""
    config.load(str(tmp_path / "nparse.config.json"))
    config.verify_settings()
    config.data["discord"]["toggled"] = True
    monkeypatch.setattr(config, "save", lambda: None)
    # The placeholder path, so the test never spins up a browser engine.
    monkeypatch.setattr(discord_module, "QWebEngineView", None)
    app = QApplication.instance()
    if not hasattr(app, "_signals"):
        app._signals = {"settings": SettingsSignals()}
    window = Discord()
    qtbot.addWidget(window)
    return window


def test_the_discord_window_is_created_with_an_alpha_channel(discord_open) -> None:
    """WA_TranslucentBackground only reaches the surface format of the window
    created AFTER it is set, so setting it later (as __init__ did) left the
    request permanently ungranted."""
    handle = discord_open.windowHandle()
    assert handle is not None, "the window should be shown, and so have a platform window"
    assert handle.format().alphaBufferSize() > 0


def test_the_alpha_channel_does_not_wait_for_a_settings_apply(discord_open) -> None:
    """The bug's signature: it took an Apply (any Apply — it only had to change
    a window flag) to recreate the window and grant the alpha. Nothing here
    calls apply_window_state, and the channel is already there."""
    before = discord_open.windowHandle().format().alphaBufferSize()
    discord_open.apply_window_state()  # what Settings > Windows does on Apply
    assert before == discord_open.windowHandle().format().alphaBufferSize() > 0
