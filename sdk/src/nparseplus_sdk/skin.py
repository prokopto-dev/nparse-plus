"""What nParse+ looks like, re-exported from the running host.

An add-on that wants to sit beside the Timers window without looking like a
bug report needs the app's colours, its type scale and its frame. Hardcoding
hex goes stale the moment the user switches skin — and the switch is **live**,
mid-fight, with no restart — while importing ``nparseplus.ui.skins`` reaches
into host internals that move with every design pass.

This module is the supported answer: a small, frozen read surface, forwarded
lazily to the host like :mod:`nparseplus_sdk.events` so importing it never
pulls Qt. The forwarded set is the explicit :data:`EXPORTS` allowlist, so the
SDK's additive-only 1.x promise covers names chosen here rather than
everything that happens to live in the host's skin layer.

The one rule to take away
-------------------------

**The palette owns VALUE; the skin owns HUE.**

:attr:`AppSkin.text`, :attr:`~AppSkin.surface`, :attr:`~AppSkin.field_bg` and
the rest of the value group are the readability floor. They are *identical*
under all three skins, and they are what you paint text and grounds with. The
skin contributes exactly one hue — :attr:`~AppSkin.accent` — for selection
bands, focus rings, group titles and hairlines.

So this is right::

    label.setStyleSheet(f"color: {app.text}; background: {app.surface};")
    rule.setStyleSheet(f"background: {app.accent};")     # a hairline

and this is the mistake the split exists to prevent::

    label.setStyleSheet(f"color: {app.text}; background: {app.accent};")

because under Velious ``accent`` is ``#e2c882`` — gold — and the app's own
title caps are gold too, so a plugin painting a ground with it produces
gold-on-gold and an unreadable window on exactly one of the three skins.

Using it
--------

Read the snapshot at the moment you paint, never at ``activate``::

    from nparseplus_sdk import skin

    class MyWindow(PluginWindow):
        def apply_skin(self) -> None:
            super().apply_skin()               # the app's overlay dressing
            app = skin.current()
            self._total.setStyleSheet(
                app.typography(skin.NUMERIC_TEXT, color=app.heading)
            )
            self._bar.setStyleSheet(app.bar_stylesheet(skin.GOOD))

``PluginWindow.apply_skin()`` is called on every skin, font-size and
frame-opacity change, so re-dressing in place there is the whole contract.
Sizes are multipliers of the user's ``general.font_size`` — use
:meth:`AppSkin.px` and :meth:`AppSkin.typography`, never literal px.

See ``docs/plugins/appearance.md`` for the worked example.
"""

from __future__ import annotations

from typing import Any

#: The host names this module forwards. Anything else raises AttributeError.
EXPORTS = frozenset(
    {
        # the snapshot
        "AppSkin",
        "current",
        "SKIN_NAMES",
        # typography
        "TypographyRole",
        "SMALL_DISPLAY",
        "BODY_TEXT",
        "NUMERIC_TEXT",
        "px",
        "tracking",
        "typography_style",
        # the object names the ready-made stylesheets target
        "TITLE",
        "ROW_NAME",
        "ROW_VALUE",
        # semantic accents
        "GOOD",
        "BAD",
        "COOLDOWN",
        "TIMER",
        "ROLL",
        "POP_WINDOW",
        "LINK",
        # colour helpers
        "shade",
        "rgba",
        "gradient",
    }
)

_HOST_HINT = (
    "nparseplus_sdk.skin re-exports the host app's skin values and needs "
    "nparseplus importable. Inside nParse+ this always works; for standalone "
    "development install the app from source: "
    "pip install git+https://github.com/prokopto-dev/nparse-plus"
)


def _host_skin() -> Any:
    try:
        from nparseplus.ui import pluginskin
    except ImportError as exc:  # pragma: no cover - depends on environment
        raise ImportError(_HOST_HINT) from exc
    return pluginskin


def __getattr__(name: str) -> Any:
    if name not in EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(_host_skin(), name)


def __dir__() -> list[str]:
    return sorted(EXPORTS | {"EXPORTS"})
