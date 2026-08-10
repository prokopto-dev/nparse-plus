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

import re
from dataclasses import dataclass
from enum import IntEnum

_HEADER = ("Location", "Name", "ID")

#: The client writes bag slots as ``General1-Slot1``; the C# enum this port
#: mirrors spells them ``General1Slot1`` because a dash cannot be an
#: identifier. Only the ordinal is the enum's business — the label belongs to
#: the file, and p99planner will not place an item whose slot is spelled the
#: enum's way.
_SLOT_DASH = re.compile(r"(?<=\d)Slot(?=\d)")


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


def canonical_location_name(name: str) -> str:
    """A location label in the spelling the client itself writes.

    ``General1Slot1`` -> ``General1-Slot1``; anything already dashed, and
    every slotless location, is returned untouched. Used for labels that came
    from the enum rather than from a file — see :data:`_SLOT_DASH`.
    """
    return _SLOT_DASH.sub("-Slot", name)


@dataclass(frozen=True)
class InventoryItem:
    location: int  # InventoryLocation wire ordinal
    name: str
    item_id: int
    count: int
    slots: int
    #: The Location cell verbatim, because the ordinal cannot round-trip it:
    #: the dash is lost on the way in, and locations the C# enum never had
    #: (shared-bank slots) would come back out as ``Unknown``. The wire
    #: (pigparse) still speaks ordinals; text exports use this.
    location_label: str = ""


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
        label = parts[0].strip()
        location = _LOCATION_BY_KEY.get(label.replace("-", "").lower(), InventoryLocation.Unknown)
        items.append(
            InventoryItem(
                location=int(location),
                name=parts[1],
                item_id=item_id,
                count=count,
                slots=slots,
                location_label=label,
            )
        )
    return items or None
