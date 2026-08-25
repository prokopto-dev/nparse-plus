"""Qt pieces of the merchant-prices plugin (imported only inside the app).

Doubles as the reference for ``nparseplus_sdk.skin``: the window reads the
app's own colours and type instead of hardcoding any, so it matches whichever
of the three skins the user is running and follows a change live.

The rule the styling below obeys, and the one worth copying:
**the palette owns VALUE, the skin owns HUE.** Text and grounds come from the
value group (``app.text``, ``app.hint``, ``app.field_bg``) — identical under
every skin, so they are always readable. The skin's ``accent`` appears only as
an accent: the grid hairline, the selection band, the button edge. Painting a
ground with it would give gold text on a gold field under Velious.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QTimer
from PySide6.QtGui import QColor
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

from nparseplus_sdk import skin
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

        # Stamp one of the skin's own object names and the label wears the
        # window-title treatment — caps, tracking, the skin's title colour —
        # with no rules of our own. The names come from the façade so a
        # rename in the host cannot leave this silently undressed.
        self._title = QLabel("Merchant Prices", self)
        self._title.setObjectName(skin.TITLE)

        self._table = QTableWidget(0, 2, self)
        self._table.setHorizontalHeaderLabels(("Item", "6-mo avg"))
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._empty = QLabel("Auction something (WTS …) to start tracking.", self)
        self._empty.setObjectName("MerchantPricesEmpty")
        self._empty.setWordWrap(True)
        clear = QPushButton("Clear tracked items", self)
        clear.clicked.connect(self._plugin.clear_items)

        layout = QVBoxLayout()
        layout.addWidget(self._title)
        layout.addWidget(self._empty)
        layout.addWidget(self._table, 1)
        layout.addWidget(clear)
        self.setLayout(layout)

        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self._on_refresh_tick)
        self._refresh_timer.start(REFRESH_INTERVAL_MS)

        self.refresh()
        self.restore_visibility()

    def skin_stylesheet(self) -> str:
        """Our own rules, appended after the app's overlay dressing.

        The hook rather than ``setStyleSheet``: PluginWindow owns the whole
        sheet and re-assembles it from the two halves on every skin, font
        size and frame-opacity change, so this is called afresh each time and
        must never cache the snapshot. It also runs from ``__init__`` before
        our widgets exist — return rules, do not touch widgets.
        """
        app = skin.current()
        return (
            # Ground and text: the value group, identical under every skin.
            f"QTableWidget {{ background: transparent; border: 0; color: {app.text}; "
            f"gridline-color: {skin.rgba(app.accent, 0.25)}; }}"
            # A selection is the skin's own band, with a PALETTE foreground on
            # it — an accent-coloured one measures 2.9:1 under Ledger.
            f"QTableWidget::item:selected {{ background: {app.gradient(app.band)}; "
            f"color: {app.heading}; }}"
            f"QHeaderView::section {{ background: {app.surface_alt}; border: 0; "
            f"border-bottom: 1px solid {app.hairline}; padding: {app.px(0.3)}px; "
            # Sizes are multipliers of the user's font size, never px.
            f"{app.typography(skin.SMALL_DISPLAY, color=app.accent)} }}"
            f"QPushButton {{ background: {app.field_bg}; color: {app.text}; "
            f"border: 1px solid {app.plate_border}; padding: {app.px(0.35)}px; }}"
            f"QPushButton:hover {{ background: {skin.rgba(app.accent, 0.14)}; }}"
            f"#MerchantPricesEmpty {{ color: {app.hint}; }}"
        )

    def apply_skin(self) -> None:
        """What a stylesheet cannot do: the price cells carry colours of
        their own, so they have to be rebuilt rather than restyled — a sheet
        swap alone would leave the last skin's ink in the table."""
        super().apply_skin()
        self._rendered_version = -1
        self.refresh()

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
        app = skin.current()
        for index, (name, average) in enumerate(rows):
            self._table.setItem(index, 0, QTableWidgetItem(name))
            price = format_platinum(average) if average is not None else "…"
            cell = QTableWidgetItem(price)
            # A resolved price is what the eye is here for; one still being
            # fetched steps back. Both are palette-owned values, so the pair
            # stays legible whichever skin is on.
            cell.setForeground(QColor(app.heading if average is not None else app.hint))
            self._table.setItem(index, 1, cell)

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
