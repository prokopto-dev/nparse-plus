"""PluginWindow — the overlay window base plugins subclass.

Re-exported to plugin authors as ``nparseplus_sdk.ui.PluginWindow``. It is a
thin adapter from a :class:`~nparseplus_sdk.plugin.PluginWindowContext` onto
``OverlayWindowBase``, which supplies the full nParse+ overlay recipe:
frameless + drag-to-move, resize from any edge, opacity/on-top/click-through
flags, per-window geometry persisted in ``Settings.windows[window_key]``,
and quit safety (Cmd+Q never clobbers the shown flag).

Subclasses build their content in ``__init__`` and finish with
``self.restore_visibility()``. For live bus events, connect to
``self.window_context.bridge.event_received`` (GUI-thread delivery); for
state polling, use a QTimer gated on ``isVisible()``.
"""

from __future__ import annotations

from nparseplus.config.settings import WindowState
from nparseplus.ui.overlaybase import OverlayWindowBase
from nparseplus_sdk.plugin import PluginWindowContext


class PluginWindow(OverlayWindowBase):
    def __init__(
        self,
        wctx: PluginWindowContext,
        *,
        translucent: bool = True,
        default_state: WindowState | None = None,
        parent=None,
    ) -> None:
        super().__init__(
            settings=wctx.settings,
            window_key=wctx.window_key,
            title=wctx.title,
            default_geometry=wctx.default_geometry,
            on_save=wctx.on_save,
            default_state=default_state,
            translucent=translucent,
            parent=parent,
        )
        self.window_context = wctx
