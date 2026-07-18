"""Named window-position/size presets and their system-tray menu."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from PySide6.QtWidgets import QApplication, QInputDialog, QMenu, QMessageBox, QWidget

from nparseplus.config.settings import Settings, WindowLayoutPreset, WindowState

LEGACY_WINDOW_KEYS = frozenset({"maps", "discord"})


def clamp_rect_to_screen(
    rect: tuple[int, int, int, int], screen: tuple[int, int, int, int]
) -> tuple[int, int, int, int]:
    """Move/shrink ``rect`` so it lies fully within ``screen`` (x, y, w, h)."""
    x, y, w, h = rect
    sx, sy, sw, sh = screen
    w = min(w, sw)
    h = min(h, sh)
    x = max(sx, min(x, sx + sw - w))
    y = max(sy, min(y, sy + sh - h))
    return (x, y, w, h)


class WindowLayoutManager:
    """Capture, apply, and manage named layouts across new and legacy windows."""

    def __init__(
        self,
        settings: Settings,
        windows: Mapping[str, QWidget],
        *,
        on_save: Callable[[], None],
        legacy_config: dict[str, Any] | None = None,
        on_legacy_save: Callable[[], None] | None = None,
        notify: Callable[[str], None] | None = None,
    ) -> None:
        self._settings = settings
        self._windows = dict(windows)
        self._on_save = on_save
        self._legacy = legacy_config if legacy_config is not None else {}
        self._on_legacy_save = on_legacy_save
        self._notify = notify

    @property
    def names(self) -> list[str]:
        return sorted(self._settings.window_layouts, key=str.casefold)

    def _matching_name(self, name: str) -> str | None:
        folded = name.casefold()
        return next(
            (saved for saved in self._settings.window_layouts if saved.casefold() == folded),
            None,
        )

    @staticmethod
    def _clean_name(name: str) -> str:
        cleaned = name.strip()
        if not cleaned:
            raise ValueError("Layout names cannot be blank.")
        return cleaned

    def save_layout(self, name: str, *, overwrite: bool = False) -> str:
        """Capture current geometry for every managed window."""
        cleaned = self._clean_name(name)
        existing = self._matching_name(cleaned)
        if existing is not None and not overwrite:
            raise ValueError(f'A layout named "{existing}" already exists.')
        saved_name = existing or cleaned
        geometries: dict[str, tuple[int, int, int, int]] = {}
        for key, window in self._windows.items():
            geometry = window.geometry()
            geometries[key] = (
                geometry.x(),
                geometry.y(),
                geometry.width(),
                geometry.height(),
            )
        self._settings.window_layouts[saved_name] = WindowLayoutPreset(geometries=geometries)
        self._on_save()
        return saved_name

    def apply_layout(self, name: str) -> None:
        """Apply a saved layout live and persist it as the current geometry."""
        saved_name = self._matching_name(name)
        if saved_name is None:
            raise KeyError(name)
        preset = self._settings.window_layouts[saved_name]
        legacy_changed = False
        for key, geometry in preset.geometries.items():
            window = self._windows.get(key)
            if window is None:
                continue
            window.setGeometry(*geometry)
            if key in LEGACY_WINDOW_KEYS:
                self._legacy.setdefault(key, {})["geometry"] = list(geometry)
                legacy_changed = True
            else:
                self._settings.windows.setdefault(key, WindowState()).geometry = geometry
        if legacy_changed and self._on_legacy_save is not None:
            self._on_legacy_save()
        self._on_save()
        self._send_notification(f'Applied "{saved_name}".')

    def reset_onscreen(self) -> None:
        """Clamp every managed window fully onto a visible screen and persist it.

        Manual only (menu action). Never shows hidden windows or alters visibility.
        """
        legacy_changed = False
        for key, window in self._windows.items():
            screen = QApplication.screenAt(window.frameGeometry().center())
            if screen is None:
                screen = QApplication.primaryScreen()
            available = screen.availableGeometry()
            geometry = window.geometry()
            clamped = clamp_rect_to_screen(
                (geometry.x(), geometry.y(), geometry.width(), geometry.height()),
                (available.x(), available.y(), available.width(), available.height()),
            )
            window.setGeometry(*clamped)
            if key in LEGACY_WINDOW_KEYS:
                self._legacy.setdefault(key, {})["geometry"] = list(clamped)
                legacy_changed = True
            else:
                self._settings.windows.setdefault(key, WindowState()).geometry = clamped
        if legacy_changed and self._on_legacy_save is not None:
            self._on_legacy_save()
        self._on_save()
        self._send_notification("Window positions reset.")

    def rename_layout(self, old_name: str, new_name: str) -> str:
        old_saved_name = self._matching_name(old_name)
        if old_saved_name is None:
            raise KeyError(old_name)
        cleaned = self._clean_name(new_name)
        conflict = self._matching_name(cleaned)
        if conflict is not None and conflict != old_saved_name:
            raise ValueError(f'A layout named "{conflict}" already exists.')
        preset = self._settings.window_layouts.pop(old_saved_name)
        self._settings.window_layouts[cleaned] = preset
        self._on_save()
        return cleaned

    def delete_layout(self, name: str) -> None:
        saved_name = self._matching_name(name)
        if saved_name is None:
            raise KeyError(name)
        del self._settings.window_layouts[saved_name]
        self._on_save()

    def populate_menu(self, parent: QMenu) -> QMenu:
        """Add the complete Window Layouts submenu to a tray menu."""
        # Construct with explicit parents. PySide can prematurely invalidate
        # wrappers returned by addMenu(str) for menus nested more than once.
        menu = QMenu("Window Layouts", parent)
        parent.addMenu(menu)
        menu.addAction("Save Current Layout…").triggered.connect(self._prompt_save)
        menu.addAction("Reset Window Positions").triggered.connect(
            lambda _checked=False: self.reset_onscreen()
        )
        menu.addSeparator()
        if not self.names:
            empty = menu.addAction("No saved layouts")
            empty.setEnabled(False)
            return menu

        for name in self.names:
            layout_menu = QMenu(name, menu)
            menu.addMenu(layout_menu)
            layout_menu.addAction("Apply").triggered.connect(
                lambda _checked=False, saved=name: self.apply_layout(saved)
            )
            layout_menu.addAction("Replace with Current Layout").triggered.connect(
                lambda _checked=False, saved=name: self._prompt_replace(saved)
            )
            layout_menu.addSeparator()
            layout_menu.addAction("Rename…").triggered.connect(
                lambda _checked=False, saved=name: self._prompt_rename(saved)
            )
            layout_menu.addAction("Delete…").triggered.connect(
                lambda _checked=False, saved=name: self._prompt_delete(saved)
            )
        return menu

    def _prompt_save(self) -> None:
        name, accepted = QInputDialog.getText(None, "Save Window Layout", "Layout name:")
        if not accepted or not name.strip():
            return
        existing = self._matching_name(name.strip())
        if existing is not None:
            answer = QMessageBox.question(
                None,
                "Replace Window Layout",
                f'Replace the saved layout "{existing}" with the current window positions?',
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        saved_name = self.save_layout(name, overwrite=existing is not None)
        self._send_notification(f'Saved "{saved_name}".')

    def _prompt_replace(self, name: str) -> None:
        answer = QMessageBox.question(
            None,
            "Replace Window Layout",
            f'Replace "{name}" with the current window positions?',
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.save_layout(name, overwrite=True)
            self._send_notification(f'Updated "{name}".')

    def _prompt_rename(self, name: str) -> None:
        new_name, accepted = QInputDialog.getText(
            None, "Rename Window Layout", "New name:", text=name
        )
        if not accepted or new_name.strip() == name:
            return
        try:
            renamed = self.rename_layout(name, new_name)
        except ValueError as error:
            QMessageBox.warning(None, "Rename Window Layout", str(error))
            return
        self._send_notification(f'Renamed "{name}" to "{renamed}".')

    def _prompt_delete(self, name: str) -> None:
        answer = QMessageBox.question(
            None,
            "Delete Window Layout",
            f'Delete the saved layout "{name}"?',
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.delete_layout(name)
            self._send_notification(f'Deleted "{name}".')

    def _send_notification(self, message: str) -> None:
        if self._notify is not None:
            self._notify(message)
