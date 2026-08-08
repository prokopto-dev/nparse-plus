"""Settings > Plugins — the list of registries add-ons may be offered from.

A registry is a trust decision one level above a plugin: it decides which
add-ons you are ever shown, and the sha256 it publishes only pins a download
to what *that* registry chose. So adding one goes through the same
explicit, default-to-cancel confirmation as enabling a plugin, and the
built-in registry can be unticked but never removed — there would be no way
back to it from this UI.

Kept out of ``pluginmanager`` so the warning text and the add flow can be
imported (and monkeypatched) without pulling in the manager page.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QInputDialog,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from nparseplus.ui import chromewidgets
from nparseplus.ui.pluginconsent import CONSENT_WARNING

if TYPE_CHECKING:
    from nparseplus.core.plugins.host import PluginHost
    from nparseplus.core.plugins.registry import ResolvedRegistry

REGISTRY_WARNING = (
    "A plugin registry decides which add-ons nParse+ offers you, and whoever "
    "runs it can list any code at all. The sha256 in a listing only proves a "
    "download matches what that registry says it should be — it is not a "
    "review and it says nothing about what the code does. Only add "
    "registries you trust."
)

_COLUMNS = ("Enabled", "Name", "URL")
_ENABLED_COLUMN = 0
_NAME_COLUMN = 1
_URL_COLUMN = 2
# The registry list is a footnote to the plugin table above it: show a few
# rows and scroll, so the plugin table keeps the page's vertical stretch.
_VISIBLE_ROWS = 3

_DEFAULT_ROW_TIP = "Ships with nParse+. It can be unticked, but not removed."


def registry_confirm_text(url: str, name: str = "") -> str:
    """The body of the add-a-registry confirmation. Pure, so it can be tested.

    Both warnings appear: the registry-level one (what a registry decides,
    and the limit of the hash), then the plugin-level one it leads to.
    """
    label = name.strip()
    heading = f"Add '{label}' as a plugin registry?" if label else "Add this plugin registry?"
    return "\n\n".join([f"{heading}\n{url}", REGISTRY_WARNING, CONSENT_WARNING])


def confirm_add_registry(parent: QWidget | None, url: str, name: str = "") -> bool:
    """Modal add confirmation; True = the user accepted. Defaults to Cancel.

    Module-level rather than a method so the add flow can be driven in tests
    without a live message box.
    """
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Warning)
    box.setWindowTitle("Add plugin registry?")
    box.setText(registry_confirm_text(url, name))
    add = box.addButton("Add registry", QMessageBox.ButtonRole.AcceptRole)
    box.setDefaultButton(box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole))
    box.exec()
    return box.clickedButton() is add


class RegistryListWidget(QWidget):
    """Enable/add/remove the registries the Browse dialog merges."""

    def __init__(self, host: PluginHost, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._host = host
        # Row order mirrors this list exactly (built-in first) — the table is
        # a pure rendering of it, so a row index is a registry index.
        self._registries: list[ResolvedRegistry] = []

        self._table = QTableWidget(0, len(_COLUMNS), self)
        self._table.setHorizontalHeaderLabels(_COLUMNS)
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.itemSelectionChanged.connect(self._sync_buttons)
        self._table.setFixedHeight(
            self._table.horizontalHeader().height()
            + _VISIBLE_ROWS * self._table.verticalHeader().defaultSectionSize()
            + 4
        )

        self._add_button = QPushButton("Add registry…", self)
        self._add_button.clicked.connect(self._add)
        self._remove_button = QPushButton("Remove", self)
        self._remove_button.clicked.connect(self._remove_selected)

        buttons = QHBoxLayout()
        buttons.addWidget(self._add_button)
        buttons.addWidget(self._remove_button)
        buttons.addStretch(1)

        note = chromewidgets.hint(
            f"Browse shows plugins from every ticked registry. {REGISTRY_WARNING}", self
        )

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._table)
        layout.addLayout(buttons)
        layout.addWidget(note)
        self.setLayout(layout)

        self.refresh()

    # --- table -------------------------------------------------------------
    def refresh(self) -> None:
        self._registries = self._host.registries()
        self._table.setRowCount(len(self._registries))
        for row, registry in enumerate(self._registries):
            box = QCheckBox(self)
            box.setChecked(registry.enabled)
            # Connected after setChecked: restoring the stored state must
            # never look like a user toggle and write settings back.
            box.toggled.connect(
                lambda checked, url=registry.url: self._host.set_registry_enabled(url, checked)
            )
            if registry.is_default:
                box.setToolTip(_DEFAULT_ROW_TIP)
            self._table.setCellWidget(row, _ENABLED_COLUMN, box)
            name_tip = _DEFAULT_ROW_TIP if registry.is_default else REGISTRY_WARNING
            for column, text, tooltip in (
                (_NAME_COLUMN, registry.name, name_tip),
                (_URL_COLUMN, registry.url, registry.url),
            ):
                item = QTableWidgetItem(text)
                item.setToolTip(tooltip)
                self._table.setItem(row, column, item)
        self._sync_buttons()

    def _selected_registry(self) -> ResolvedRegistry | None:
        row = self._table.currentRow()
        if 0 <= row < len(self._registries):
            return self._registries[row]
        return None

    def _sync_buttons(self) -> None:
        """First guard on Remove: the built-in row can't even arm the button."""
        registry = self._selected_registry()
        self._remove_button.setEnabled(registry is not None and not registry.is_default)

    # --- actions -----------------------------------------------------------
    def _add(self) -> None:
        url, ok = QInputDialog.getText(
            self, "Add plugin registry", "https:// URL of a registry index.json:"
        )
        if not ok or not url.strip():
            return
        url = url.strip()
        name, ok = QInputDialog.getText(self, "Add plugin registry", "Display name (optional):")
        if not ok:
            return
        name = name.strip()
        # Nothing is written until the warning is accepted — add_registry
        # persists, so it is the last call in the flow, not the first.
        if not confirm_add_registry(self, url, name):
            return
        error = self._host.add_registry(url, name)
        if error is not None:
            QMessageBox.warning(self, "Registry not added", error)
            return
        self.refresh()

    def _remove_selected(self) -> None:
        registry = self._selected_registry()
        if registry is None:
            return
        if registry.is_default:
            # Second guard, independent of the disabled button: a keyboard
            # path or a stale selection must not delete the way back in.
            QMessageBox.information(
                self,
                "Remove registry",
                "The built-in nParse+ registry cannot be removed. "
                "Untick it to stop offering plugins from it.",
            )
            return
        confirm = QMessageBox.question(
            self,
            "Remove registry?",
            f"Stop offering plugins from {registry.name}?\n({registry.url})\n\n"
            "Plugins already installed from it stay installed.",
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        self._host.remove_registry(registry.url)
        self.refresh()
