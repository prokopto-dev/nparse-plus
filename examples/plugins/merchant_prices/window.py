"""Qt pieces of the merchant-prices plugin (imported only inside the app)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QFormLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from nparseplus_sdk.ui import PluginWindow

from .pricing import format_platinum

if TYPE_CHECKING:
    from . import MerchantPricesPlugin

REFRESH_INTERVAL_MS = 1000


class MerchantPricesWindow(PluginWindow):
    """Overlay listing tracked WTS items with their 6-month PigParse average."""

    def __init__(self, wctx: Any, plugin: MerchantPricesPlugin) -> None:
        super().__init__(wctx)
        self._plugin = plugin
        self._rendered_version = -1

        self._table = QTableWidget(0, 2, self)
        self._table.setHorizontalHeaderLabels(("Item", "6-mo avg"))
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._empty = QLabel("Auction something (WTS …) to start tracking.", self)
        self._empty.setWordWrap(True)
        clear = QPushButton("Clear tracked items", self)
        clear.clicked.connect(self._plugin.clear_items)

        layout = QVBoxLayout()
        layout.addWidget(self._empty)
        layout.addWidget(self._table, 1)
        layout.addWidget(clear)
        self.setLayout(layout)

        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self._on_refresh_tick)
        self._refresh_timer.start(REFRESH_INTERVAL_MS)

        self.refresh()
        self.restore_visibility()

    def _on_refresh_tick(self) -> None:
        if self.isVisible():  # no work while hidden (DPS-window pattern)
            self.refresh()

    def refresh(self) -> None:
        version, rows = self._plugin.snapshot()
        if version == self._rendered_version:
            return
        self._rendered_version = version
        self._empty.setVisible(not rows)
        self._table.setVisible(bool(rows))
        self._table.setRowCount(len(rows))
        for index, (name, average) in enumerate(rows):
            self._table.setItem(index, 0, QTableWidgetItem(name))
            price = format_platinum(average) if average is not None else "…"
            self._table.setItem(index, 1, QTableWidgetItem(price))

    def showEvent(self, event) -> None:  # immediate repaint on reopen
        super().showEvent(event)
        self._rendered_version = -1
        self.refresh()


def build_settings_page(parent: QWidget | None, poll_seconds: int) -> QWidget:
    page = QWidget(parent)
    form = QFormLayout()
    spin = QSpinBox(page)
    spin.setRange(60, 3600)
    spin.setSuffix(" s")
    spin.setValue(poll_seconds)
    spin.setObjectName("poll_seconds")
    form.addRow("Price poll interval", spin)
    note = QLabel("How often tracked items are re-priced against PigParse.", page)
    note.setWordWrap(True)
    form.addRow(note)
    page.setLayout(form)
    return page


def read_settings_page(page: QWidget) -> int:
    spin = page.findChild(QSpinBox, "poll_seconds")
    return int(spin.value()) if spin is not None else 300
