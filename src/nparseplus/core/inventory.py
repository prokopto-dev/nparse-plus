"""Inventory dump parsing (Qt-free).

The ``/outputfile inventory`` half of EQTool's
Services/InventoryWatcherService.cs: the game writes a TSV (``Location Name
ID Count Slots``) into the EQ directory, and this reads it.

The service's other half — noticing that a dump appeared and uploading it —
is split across two modules here, because the app grew a second consumer of
the same file. :mod:`nparseplus.core.dumps` polls the EQ directory once and
keeps a per-character library of both dump kinds;
:mod:`nparseplus.core.handlers.inventory_upload` subscribes to the events it
publishes and sends the result wherever the user pointed it (``off`` by
default; pigparse.org or p99planner.com otherwise). This module stays the one
place that knows the file format, and both of them call it.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum

_HEADER = ("Location", "Name", "ID")


def _inventory_location_names() -> list[str]:
    """EQToolShared/Enums/InventoryLocation.cs, in ordinal order (the enum
    is systematic; generated rather than transcribed)."""
    names = [
        "Unknown", "Charm", "Ear", "Head", "Face", "Neck", "Shoulders", "Arms",
        "Back", "Wrist", "Range", "Hands", "Primary", "Secondary", "Fingers",
        "Chest", "Legs", "Feet", "Waist", "Ammo", "Held",
    ]  # fmt: skip
    names += [f"General{i}" for i in range(1, 9)]
    names += [f"General{i}Slot{j}" for i in range(1, 9) for j in range(1, 11)]
    names += [f"Bank{i}" for i in range(1, 17)]
    names += [f"Bank{i}Slot{j}" for i in range(1, 17) for j in range(1, 11)]
    names += ["SharedBank1", "SharedBank2"]
    return names


InventoryLocation = IntEnum(
    "InventoryLocation", {name: i for i, name in enumerate(_inventory_location_names())}
)
_LOCATION_BY_KEY = {name.lower(): member for name, member in InventoryLocation.__members__.items()}


@dataclass(frozen=True)
class InventoryItem:
    location: int  # InventoryLocation wire ordinal
    name: str
    item_id: int
    count: int
    slots: int


def parse_inventory_text(text: str) -> list[InventoryItem] | None:
    """Parse an ``/outputfile inventory`` dump; None if it isn't one."""
    lines = text.splitlines()
    if len(lines) < 2:
        return None
    header = lines[0].split("\t")
    if len(header) < 5 or tuple(header[:3]) != _HEADER:
        return None
    items: list[InventoryItem] = []
    for line in lines[1:]:
        parts = line.split("\t")
        if len(parts) < 5:
            continue
        try:
            item_id, count, slots = int(parts[2]), int(parts[3]), int(parts[4])
        except ValueError:
            continue
        location_key = parts[0].replace("-", "").lower()
        location = _LOCATION_BY_KEY.get(location_key, InventoryLocation.Unknown)
        items.append(
            InventoryItem(
                location=int(location), name=parts[1], item_id=item_id, count=count, slots=slots
            )
        )
    return items or None
