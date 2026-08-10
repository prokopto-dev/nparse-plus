"""Shared dump-library fixtures.

The sample text mirrors what a real P99 client writes: an inventory dump has
the ``Location Name ID Count Slots`` header and a row for every slot
(including the ``Empty`` ones the library drops), and a spellbook dump has no
header at all — just ``<level>\\t<name>``.
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

import pytest

INVENTORY_TEXT = (
    "Location\tName\tID\tCount\tSlots\n"
    "Charm\tEmpty\t0\t0\t0\n"
    "Ear\tTreant Tear\t12801\t1\t5\n"
    "Head\tIksar Hide Cap\t5799\t1\t5\n"
    "Wrist\tWoven Grass Bracelet\t31150\t1\t5\n"
    "Wrist\tWoven Grass Bracelet\t31150\t1\t5\n"
    "General1\tLarge Bag\t17969\t1\t8\n"
    "General1-Slot1\tRusty Sword\t5678\t1\t0\n"
)

SPELLBOOK_TEXT = (
    "51\tSuperior Healing\n49\tShield of Thorns\n14\tSpirit of Wolf\n29\tEnsnare\n44\tChloroplast\n"
)

T0 = datetime(2026, 8, 9, 12, 0, 0)


def write_dump(directory: Path, name: str, text: str, *, when: datetime | None = None) -> Path:
    """Write a dump file and stamp its mtime (the library's capture time)."""
    path = Path(directory) / name
    path.write_text(text, encoding="utf-8")
    if when is not None:
        stamp = when.timestamp()
        os.utime(path, (stamp, stamp))
    return path


@pytest.fixture
def eq_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "EverQuest"
    directory.mkdir()
    # Unrelated files the real directory is full of; none may be read.
    (directory / "AutoChannels.txt").write_text("noise\n", encoding="utf-8")
    (directory / "RaceData.txt").write_text("1\tHuman\n", encoding="utf-8")
    return directory


@pytest.fixture
def library_root(tmp_path: Path) -> Path:
    return tmp_path / "library"
