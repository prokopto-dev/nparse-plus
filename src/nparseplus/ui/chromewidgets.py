"""The Qt half of the chrome layer — label factories and the live-apply mixin.

``ui/chrome.py`` is pure data and stylesheet strings. This is where those meet
real widgets: the three label factories that stamp the object names the sheet
targets, the property flips that need a repolish, and (later) the mixin every
config window uses to re-dress itself on a skin change.

Deliberately not folded into ``ui/skinwidgets.py``: that module is the painted
half of the skin layer — everything in it overrides ``paintEvent`` to draw what
a stylesheet cannot express. A hint label paints nothing.
"""

from __future__ import annotations

from PySide6.QtWidgets import QLabel, QWidget

from nparseplus.ui import chrome, skins, theme
from nparseplus.ui.skinwidgets import set_caps

#: Font size the factories dress at before a window sheet takes over. Matches
#: ``GeneralSettings.font_size``'s default; Phase-3 windows pass the real one.
_FALLBACK_FONT_SIZE = 12


def current() -> chrome.Chrome:
    """Chrome tokens for the active skin and theme.

    The one read of module state in this layer — ``chrome.py`` itself takes
    both as arguments so it stays pure and testable.
    """
    return chrome.chrome_for(skins.skin(), theme.palette())


def _dress(label: QLabel, style: str) -> None:
    """Apply a per-widget sheet so the factories work before the window sheet.

    TEMPORARY. Once the config windows carry ``chrome.window_style`` these
    rules arrive from the window and this must go — a widget-level sheet beats
    a window-level one, so leaving it here would make a hint the one label a
    live skin change cannot re-dress.
    """
    label.setStyleSheet(style)


def repolish(widget: QWidget) -> None:
    """Re-evaluate a widget's stylesheet after a dynamic property changed.

    Qt matches property selectors when it polishes, not when the property is
    set, so a ``setProperty`` alone leaves the old look on screen. Every
    ``PROP_*`` flip has to be followed by this.
    """
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()


def hint(text: str, parent: QWidget | None = None) -> QLabel:
    """A de-emphasised caption under a field.

    Wraps by default: these are explanatory sentences, and a settings page is
    narrow enough that the un-wrapped ones were widening their whole column.
    """
    label = QLabel(text, parent)
    label.setObjectName(chrome.HINT)
    label.setWordWrap(True)
    _dress(label, chrome.hint_style(current(), _FALLBACK_FONT_SIZE))
    return label


def caption(text: str, parent: QWidget | None = None) -> QLabel:
    """A SECTION CAPS label above a group of controls."""
    label = QLabel(text, parent)
    label.setObjectName(chrome.CAPTION)
    set_caps(label)
    _dress(label, chrome.caption_style(current(), _FALLBACK_FONT_SIZE))
    return label


def badge(parent: QWidget | None = None) -> QLabel:
    """A status pill. Starts empty and untoned; see :func:`set_badge`."""
    label = QLabel("", parent)
    label.setObjectName(chrome.BADGE)
    _dress(label, chrome.badge_rules(current(), _FALLBACK_FONT_SIZE))
    return label


def set_badge(label: QLabel, text: str, tone: str = "") -> None:
    """Set a pill's text and tone.

    The tone is a property, not an inline stylesheet, so the pill re-themes on
    a skin change and reads correctly in the light theme without the caller
    doing anything. An unknown tone falls back to untoned rather than raising —
    a status label is not worth a crash.
    """
    label.setText(text)
    label.setProperty(chrome.PROP_TONE, tone if tone in chrome.BADGE_TONES else "")
    repolish(label)
