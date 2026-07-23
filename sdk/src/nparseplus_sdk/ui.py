"""Qt window base for plugins, re-exported from the running nParse+ host.

``PluginWindow`` subclasses the host's overlay recipe (frameless, drag to
move, resize from any edge, opacity/on-top, geometry persisted per window
key, quit safety). It is defined in the host (``nparseplus.ui.pluginwindow``)
because it needs PySide6; this module forwards to it lazily so importing
``nparseplus_sdk`` itself never pulls Qt.

Usage inside a window factory::

    from nparseplus_sdk.ui import PluginWindow

    class MyWindow(PluginWindow):
        def __init__(self, wctx):
            super().__init__(wctx)
            ...build content...
            self.restore_visibility()
"""

from __future__ import annotations

from typing import Any

_HOST_HINT = (
    "nparseplus_sdk.ui re-exports the host app's PluginWindow base and needs "
    "nparseplus (with PySide6) importable. Import it inside your window "
    "factory — not at plugin module top level — so the validate CLI and "
    "Qt-free tests can still import your plugin."
)


def __getattr__(name: str) -> Any:
    if name == "PluginWindow":
        try:
            from nparseplus.ui.pluginwindow import PluginWindow
        except ImportError as exc:  # pragma: no cover - depends on environment
            raise ImportError(_HOST_HINT) from exc
        return PluginWindow
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return ["PluginWindow"]
