"""The Qt half of the chrome layer — label factories and the live-apply mixin.

``ui/chrome.py`` is pure data and stylesheet strings. This is where those meet
real widgets: the label factories that stamp the object names the sheet
targets, the property flips that need a repolish, the mixin every config window
uses to re-dress itself on a skin change, and the QPalette the Fusion style
reads for the control internals a stylesheet cannot reach.

Deliberately not folded into ``ui/skinwidgets.py``: that module is the painted
half of the skin layer — everything in it overrides ``paintEvent`` to draw what
a stylesheet cannot express. A hint label paints nothing.
"""

from __future__ import annotations

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QLabel, QWidget

from nparseplus.ui import chrome, skins, theme
from nparseplus.ui.skinwidgets import set_caps


def current() -> chrome.Chrome:
    """Chrome tokens for the active skin and theme.

    The one read of module state in this layer — ``chrome.py`` itself takes
    both as arguments so it stays pure and testable.
    """
    return chrome.chrome_for(skins.skin(), theme.palette())


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
    return label


def caption(text: str, parent: QWidget | None = None) -> QLabel:
    """A SECTION CAPS label above a group of controls."""
    label = QLabel(text, parent)
    label.setObjectName(chrome.CAPTION)
    set_caps(label)
    return label


def badge(parent: QWidget | None = None) -> QLabel:
    """A status pill. Starts empty and untoned; see :func:`set_badge`."""
    label = QLabel("", parent)
    label.setObjectName(chrome.BADGE)
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


# -- the live-apply seam ---------------------------------------------------------


def build_qpalette(spec: dict[str, str]) -> QPalette:
    """A QPalette from :func:`chrome.qt_palette_spec`.

    Unknown role names are skipped rather than raising: the spec is data, and
    a Qt version that drops a role should not stop the app from starting.
    """
    result = QPalette()
    for name, value in spec.items():
        role = getattr(QPalette.ColorRole, name, None)
        if role is not None:
            result.setColor(role, QColor(value))
    return result


def apply_app_palette(app: QApplication, font_size: int) -> None:
    """Point the application's QPalette at the active skin and theme.

    Paired with the Fusion style in ``app.create_app``. Without this, a dark
    chrome ground would be drawn with the platform's native (light) combo
    boxes and spin buttons inside it.
    """
    app.setPalette(build_qpalette(chrome.qt_palette_spec(current())))


class ChromeMixin:
    """Gives a config window a ``apply_chrome()`` that re-dresses it in place.

    Mixed in *before* the Qt base class so the method resolution order finds
    this first. The window must call ``apply_chrome()`` at the end of its
    ``__init__`` — ``app._apply_appearance`` never runs at startup, only on a
    skin change, exactly like the overlays reading ``skins.skin()`` in theirs.
    """

    def _chrome_font_size(self) -> int:
        """The user's base font size, or the default if this window has no
        settings handle (dialogs built standalone in tests)."""
        settings = getattr(self, "_settings", None)
        general = getattr(settings, "general", None)
        return max(6, getattr(general, "font_size", 12))

    def apply_chrome(self) -> None:
        self.setStyleSheet(  # type: ignore[attr-defined]
            chrome.window_style(skins.skin(), theme.palette(), self._chrome_font_size())
        )
