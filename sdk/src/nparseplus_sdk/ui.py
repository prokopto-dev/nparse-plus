"""Qt bases for plugins, re-exported from the running nParse+ host.

``PluginWindow`` subclasses the host's overlay recipe (frameless, drag to
move, resize from any edge, opacity/on-top, geometry persisted per window
key, quit safety). ``PluginOverlayRegion`` (SDK 1.5) is its counterpart for a
region **inside** the Event Overlay. Both are defined in the host
(``nparseplus.ui.pluginwindow`` / ``nparseplus.ui.pluginregion``) because they
need PySide6; this module forwards to them lazily so importing
``nparseplus_sdk`` itself never pulls Qt.

Usage inside a window factory::

    from nparseplus_sdk.ui import PluginWindow

    class MyWindow(PluginWindow):
        def __init__(self, wctx):
            super().__init__(wctx)
            ...build content...
            self.restore_visibility()

and inside an overlay-region factory::

    from nparseplus_sdk.ui import PluginOverlayRegion

    class MyRegion(PluginOverlayRegion):
        def __init__(self, rctx):
            super().__init__(rctx)
            ...build content...

A region is **display-only** — it never receives a click. See
``docs/plugins/overlay-regions.md``.
"""

from __future__ import annotations

import importlib
from typing import Any

_HOST_HINT = (
    "nparseplus_sdk.ui re-exports the host app's Qt bases and needs "
    "nparseplus (with PySide6) importable. Import it inside your window or "
    "region factory — not at plugin module top level — so the validate CLI "
    "and Qt-free tests can still import your plugin."
)

# Attribute name -> the host module it is forwarded from. Deliberately
# PRIVATE, unlike ``skin.EXPORTS`` / ``eqfiles.EXPORTS``: those are curated
# allowlists over a wide host surface that an author may reasonably want to
# introspect, while this module forwards two class names and always will.
# Adding a public ``EXPORTS`` here would freeze a new name under the additive-
# only 1.x promise — and one whose type (a mapping) disagrees with its two
# siblings (frozensets), which is worse than not having it.
_EXPORTS = {
    "PluginWindow": "nparseplus.ui.pluginwindow",
    "PluginOverlayRegion": "nparseplus.ui.pluginregion",
}


def __getattr__(name: str) -> Any:
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    try:
        return getattr(importlib.import_module(module), name)
    except ImportError as exc:  # pragma: no cover - depends on environment
        raise ImportError(_HOST_HINT) from exc


def __dir__() -> list[str]:
    return sorted(_EXPORTS)
