"""Mob info overlay — shows the last-considered mob (MobInfoState).

Port of EQTool's UI/MobInfo.xaml: name, zone, respawn time, notable flag,
pet indicator, a button opening the mob's P99 wiki page, and — when the
network layer has enriched the state — the known-loot list with PigParse
6-month WTS average prices (clickable rows open the wiki page).
"""

from __future__ import annotations

import webbrowser
from collections.abc import Callable

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QFrame, QLabel, QPushButton, QVBoxLayout, QWidget

from nparseplus.config.settings import Settings
from nparseplus.core.handlers.consider import MobInfoState
from nparseplus.ui.overlaybase import OverlayWindowBase, format_mmss

WINDOW_KEY = "mobinfo"
REFRESH_INTERVAL_MS = 500
DEFAULT_GEOMETRY = (640, 420, 220, 150)
WIKI_BASE = "https://wiki.project1999.com"


class MobInfoWindow(OverlayWindowBase):
    def __init__(
        self,
        settings: Settings,
        mob_info: MobInfoState,
        on_save: Callable[[], None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(
            settings=settings,
            window_key=WINDOW_KEY,
            title="Mob Info",
            default_geometry=DEFAULT_GEOMETRY,
            on_save=on_save,
            parent=parent,
        )
        self._mob_info = mob_info
        self.setObjectName("MobInfoWindow")

        self._name = QLabel("Consider a mob…", self)
        self._name.setObjectName("MobInfoName")
        self._name.setWordWrap(True)
        self._detail = QLabel("", self)
        self._detail.setWordWrap(True)
        self._loot = QLabel("", self)
        self._loot.setObjectName("MobInfoLoot")
        self._loot.setWordWrap(True)
        self._loot.setTextFormat(Qt.TextFormat.RichText)
        self._loot.setOpenExternalLinks(True)
        self._loot.hide()

        self._wiki_button = QPushButton("Open wiki page", self)
        self._wiki_button.clicked.connect(self._open_wiki)
        self._wiki_button.setEnabled(False)

        container = QFrame(self)
        container.setObjectName("MobInfoContainer")
        layout = QVBoxLayout()
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(4)
        layout.addWidget(self._name)
        layout.addWidget(self._detail)
        layout.addWidget(self._loot)
        layout.addStretch(1)
        layout.addWidget(self._wiki_button)
        container.setLayout(layout)

        outer = QVBoxLayout()
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(container)
        self.setLayout(outer)

        font_size = max(8, settings.general.font_size)
        self.setStyleSheet(
            "#MobInfoContainer { background-color: rgba(0, 0, 0, 185); border-radius: 4px; }"
            f"QLabel {{ color: #dddddd; font-size: {font_size - 2}px; }}"
            f"#MobInfoName {{ color: #ffffff; font-weight: bold; font-size: {font_size}px; }}"
        )

        # Poll (cheap) rather than marshalling on_change across threads.
        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self.refresh)
        self._refresh_timer.start(REFRESH_INTERVAL_MS)

        self.restore_visibility()

    def refresh(self) -> None:
        mob = self._mob_info
        if not mob.name:
            self._name.setText("Consider a mob…")
            self._detail.setText("")
            self._loot.hide()
            self._wiki_button.setEnabled(False)
            return
        title = mob.name
        if mob.is_notable:
            title += "  ✪"
        if mob.is_pet:
            title += "  (pet)"
        self._name.setText(title)
        parts = []
        if mob.zone:
            parts.append(f"Zone: {mob.zone}")
        if mob.spawn_seconds:
            parts.append(f"Respawn: {format_mmss(mob.spawn_seconds)}")
        self._detail.setText("\n".join(parts))
        self._render_loot()
        self._wiki_button.setEnabled(not mob.is_pet)

    def _render_loot(self) -> None:
        loot = self._mob_info.loot
        if not loot:
            self._loot.hide()
            self._loot.setText("")
            return
        rows = []
        for entry in loot[:12]:
            price = f" — {entry.price}p" if entry.price and entry.price != "0" else ""
            rows.append(f'<a href="{entry.url}" style="color:#9ecfff;">{entry.name}</a>{price}')
        more = f"<br>… +{len(loot) - 12} more" if len(loot) > 12 else ""
        self._loot.setText("Known loot:<br>" + "<br>".join(rows) + more)
        self._loot.show()

    def _open_wiki(self) -> None:
        if self._mob_info.name:
            page = self._mob_info.name.strip().replace(" ", "_")
            webbrowser.open(f"{WIKI_BASE}/{page}")

    # -- test hooks ------------------------------------------------------------

    def current_name(self) -> str:
        return self._name.text()

    def current_detail(self) -> str:
        return self._detail.text()

    def wheelEvent(self, event) -> None:  # inert like the other overlays
        event.accept()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.hide()
            self.persist_state(shown=False)
        else:
            super().keyPressEvent(event)
