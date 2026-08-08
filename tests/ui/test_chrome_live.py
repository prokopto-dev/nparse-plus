"""The chrome layer against real windows.

``tests/ui/test_chrome.py`` covers the pure token/stylesheet half. This file
covers the part that only breaks with a widget in hand: that each config window
actually wears the sheet, and that a skin change re-dresses it in place.
"""

from __future__ import annotations

import pytest

from nparseplus.config.settings import Settings
from nparseplus.ui import chrome, chromewidgets, skins, theme
from nparseplus.ui.settingswindow import UnifiedSettingsWindow

pytestmark = pytest.mark.qt


def _legacy() -> dict:
    return {
        "maps": {},
        "spells": {},
        "discord": {},
        "general": {},
    }


@pytest.fixture(autouse=True)
def _restore_appearance():
    yield
    skins.set_skin(skins.DEFAULT_SKIN)


def _window(qtbot) -> UnifiedSettingsWindow:
    window = UnifiedSettingsWindow(Settings(), on_save=lambda: None, legacy_config=_legacy())
    qtbot.addWidget(window)
    return window


def test_the_settings_window_dresses_itself_at_construction(qtbot) -> None:
    """``app._apply_appearance`` never runs at startup — only on a skin change
    — so a window that waits to be told would open undressed."""
    window = _window(qtbot)
    assert f"#{chrome.HINT}" in window.styleSheet()
    assert "QWidget {" in window.styleSheet()


def test_switching_skins_re_dresses_the_settings_window(qtbot) -> None:
    window = _window(qtbot)
    skins.set_skin("duxa")
    window.apply_chrome()
    duxa = window.styleSheet()

    skins.set_skin("velious")
    window.apply_chrome()
    velious = window.styleSheet()

    assert duxa != velious
    assert skins.VELIOUS.chrome_accent in velious
    assert skins.VELIOUS.chrome_accent not in duxa


def test_the_sidebar_and_apply_button_carry_the_names_the_sheet_targets(qtbot) -> None:
    window = _window(qtbot)
    assert window._sidebar.objectName() == chrome.SIDEBAR

    from PySide6.QtWidgets import QPushButton

    primaries = [
        button
        for button in window.findChildren(QPushButton)
        if button.objectName() == chrome.PRIMARY
    ]
    assert len(primaries) == 1
    assert primaries[0].text() == "Apply && Save"


def test_the_skin_cards_select_by_property_not_an_inline_border(qtbot) -> None:
    """An inline stylesheet on the card would beat the window sheet and freeze
    the selected edge at whatever skin was active when it was set."""
    window = _window(qtbot)
    window._select_skin_card("velious")
    picked = next(c for c in window._skin_choices if c.skin_name == "velious")
    other = next(c for c in window._skin_choices if c.skin_name == "duxa")

    assert picked.property(chrome.PROP_SELECTED) is True
    assert other.property(chrome.PROP_SELECTED) is False
    assert picked.styleSheet() == ""  # the sheet does it, not the widget


def test_hint_labels_carry_no_widget_stylesheet_of_their_own(qtbot) -> None:
    """The Phase-2 factories dressed their own widgets as a stopgap. Now that
    the window carries the sheet, a leftover widget-level rule would be the one
    label a live skin change could not reach."""
    window = _window(qtbot)
    from PySide6.QtWidgets import QLabel

    hints = [label for label in window.findChildren(QLabel) if label.objectName() == chrome.HINT]
    assert hints, "expected the settings pages to carry hint labels"
    assert all(label.styleSheet() == "" for label in hints)


def test_the_font_size_setting_reaches_the_sheet(qtbot) -> None:
    settings = Settings()
    settings.general.font_size = 18
    window = UnifiedSettingsWindow(settings, on_save=lambda: None, legacy_config=_legacy())
    qtbot.addWidget(window)
    assert "font-size: 18px" in window.styleSheet()


def test_build_qpalette_skips_a_role_qt_does_not_have() -> None:
    """The spec is data; a Qt version that drops a role must not stop launch."""
    palette = chromewidgets.build_qpalette({"Window": "#101010", "NotARole": "#ffffff"})
    assert palette is not None


def test_the_console_window_uses_a_portable_monospace_stack() -> None:
    """Menlo is macOS-only; Windows and Linux fell back to a proportional face."""
    from nparseplus.ui import consolewindow

    assert consolewindow.MONOSPACE_FAMILIES[0] == "Menlo"
    assert "Consolas" in consolewindow.MONOSPACE_FAMILIES
    assert "monospace" in consolewindow.MONOSPACE_FAMILIES


# -- Qt actually has to accept the sheet -----------------------------------------


@pytest.mark.parametrize("skin_name", sorted(skins.SKINS))
def test_qt_parses_the_window_sheet(qtbot, skin_name: str) -> None:
    """Qt reports a malformed stylesheet with a runtime warning and then
    discards the WHOLE sheet — the window renders undressed while every
    string-level assertion still passes. Nothing catches that except asking
    Qt, so this asks Qt, for every skin.
    """
    from PySide6.QtCore import qInstallMessageHandler
    from PySide6.QtWidgets import QWidget

    skins.set_skin(skin_name)
    widget = QWidget()
    qtbot.addWidget(widget)

    messages: list[str] = []
    qInstallMessageHandler(lambda mode, ctx, message: messages.append(message))
    try:
        widget.setStyleSheet(chrome.window_style(skins.skin(), theme.palette(), 12))
        widget.ensurePolished()
        widget.style().polish(widget)
    finally:
        qInstallMessageHandler(None)

    assert not [m for m in messages if "Could not parse" in m], messages


def test_qt_parses_every_real_config_window(qtbot) -> None:
    from PySide6.QtCore import qInstallMessageHandler

    messages: list[str] = []
    qInstallMessageHandler(lambda mode, ctx, message: messages.append(message))
    try:
        window = _window(qtbot)
        window.ensurePolished()
        window.apply_chrome()
    finally:
        qInstallMessageHandler(None)

    assert not [m for m in messages if "Could not parse" in m], messages


# -- the factories (need a QApplication, hence this file) ------------------------


def test_the_factories_stamp_the_names_the_sheet_targets(qtbot) -> None:
    """The object-name contract between chrome.py and chromewidgets.py. If a
    factory stops stamping, the window sheet silently misses that label."""
    assert chromewidgets.hint("x").objectName() == chrome.HINT
    assert chromewidgets.caption("x").objectName() == chrome.CAPTION
    assert chromewidgets.badge().objectName() == chrome.BADGE


def test_set_badge_falls_back_rather_than_raising_on_an_unknown_tone(qtbot) -> None:
    label = chromewidgets.badge()
    chromewidgets.set_badge(label, "Up to date", "ok")
    assert label.property(chrome.PROP_TONE) == "ok"
    chromewidgets.set_badge(label, "?", "not-a-tone")
    assert label.property(chrome.PROP_TONE) == ""
    assert label.text() == "?"
