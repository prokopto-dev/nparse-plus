"""Qt window for the template plugin (imported only inside the app)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QLabel, QVBoxLayout

from nparseplus_sdk.ui import PluginWindow

if TYPE_CHECKING:
    from . import MyPlugin

REFRESH_INTERVAL_MS = 1000


class MyPluginWindow(PluginWindow):
    """Overlay recipe (frameless/drag/resize/persistence) comes from the base."""

    def __init__(self, wctx: Any, plugin: MyPlugin) -> None:
        super().__init__(wctx)
        self._plugin = plugin
        self._label = QLabel(self)
        layout = QVBoxLayout()
        layout.addWidget(self._label)
        self.setLayout(layout)

        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self._on_refresh_tick)
        self._refresh_timer.start(REFRESH_INTERVAL_MS)

        self.refresh()
        self.restore_visibility()

    def _on_refresh_tick(self) -> None:
        if self.isVisible():  # no work while hidden
            self.refresh()

    def refresh(self) -> None:
        count = self._plugin.greeting_count()
        self._label.setText(f"Greetings so far: {count}\nSay 'hello template' in game.")
