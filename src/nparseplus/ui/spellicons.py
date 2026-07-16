"""Spell gem icons from the bundled sprite sheets.

Port of the legacy ``parsers.spells.get_spell_icon`` math: the seven
``data/spells/spells0N.png`` sheets hold 40x40 icons in 6 columns, 36 icons
per sheet (the same art EQTool embeds as TGA resources). Returns cached,
scaled QPixmaps; ``None`` when the index maps past the shipped sheets.
"""

from __future__ import annotations

import math
from pathlib import Path

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QPixmap

ICON_SIZE = 15
_CELL = 40
_ICONS_PER_SHEET = 36
_SHEET_COLUMNS = 6

_sheet_cache: dict[Path, QPixmap | None] = {}
_icon_cache: dict[tuple[Path, int, int], QPixmap | None] = {}


def _sheet(path: Path) -> QPixmap | None:
    if path not in _sheet_cache:
        pixmap = QPixmap(str(path)) if path.is_file() else None
        _sheet_cache[path] = None if pixmap is None or pixmap.isNull() else pixmap
    return _sheet_cache[path]


def spell_icon_pixmap(
    icon_index: int, size: int = ICON_SIZE, sheet_dir: Path | None = None
) -> QPixmap | None:
    """The gem icon for ``Spell.spell_icon``, or None when unavailable."""
    if icon_index <= 0:
        return None
    directory = sheet_dir if sheet_dir is not None else Path("data/spells")
    sheet_number = math.ceil(icon_index / _ICONS_PER_SHEET)
    path = directory / f"spells{sheet_number:02d}.png"
    key = (path, icon_index, size)
    if key in _icon_cache:
        return _icon_cache[key]
    sheet = _sheet(path)
    if sheet is None:
        _icon_cache[key] = None
        return None
    cell = icon_index % _ICONS_PER_SHEET
    row = math.floor((cell + _SHEET_COLUMNS) / _SHEET_COLUMNS)
    col = cell % _SHEET_COLUMNS + 1
    x = (col - 1) * _CELL
    y = (row - 1) * _CELL
    pixmap = sheet.copy(QRect(x, y, _CELL, _CELL)).scaled(
        size, size, mode=Qt.TransformationMode.SmoothTransformation
    )
    _icon_cache[key] = pixmap
    return pixmap
