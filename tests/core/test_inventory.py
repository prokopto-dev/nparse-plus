"""core.inventory — the inventory dump parser (InventoryWatcherService port).

Uploading lives in tests/core/handlers/test_inventory_upload.py now: the
EQ directory is polled once, by the dump library's watcher.
"""

from __future__ import annotations

from nparseplus.core.inventory import InventoryLocation, parse_inventory_text

DUMP = (
    "Location\tName\tID\tCount\tSlots\n"
    "Charm\tGuise of the Deceiver\t1234\t1\t0\n"
    "General1-Slot1\tRusty Sword\t5678\t1\t0\n"
    "Mystery-Spot\tWeird Thing\t1\t1\t0\n"
    "General2\tLarge Bag\t17969\t1\t8\n"
    "Bad\tRow\tx\ty\tz\n"
)


def test_enum_matches_csharp_ordinals() -> None:
    assert int(InventoryLocation.Unknown) == 0
    assert int(InventoryLocation.Charm) == 1
    assert int(InventoryLocation.Held) == 20
    assert int(InventoryLocation.General1) == 21
    assert int(InventoryLocation.General1Slot1) == 29
    assert int(InventoryLocation.Bank1) == 109
    assert int(InventoryLocation.Bank1Slot1) == 125
    assert int(InventoryLocation.SharedBank2) == 286


def test_parse_inventory_text() -> None:
    items = parse_inventory_text(DUMP)
    assert items is not None
    assert [i.name for i in items] == [
        "Guise of the Deceiver",
        "Rusty Sword",
        "Weird Thing",
        "Large Bag",
    ]
    assert items[0].location == int(InventoryLocation.Charm)
    assert items[1].location == int(InventoryLocation.General1Slot1)  # dash stripped
    assert items[2].location == int(InventoryLocation.Unknown)
    assert items[3].slots == 8


def test_parse_rejects_non_inventory_text() -> None:
    assert parse_inventory_text("just a log file\nwith lines\n") is None
    assert parse_inventory_text("") is None
    assert parse_inventory_text("Location\tName\tID\tCount\tSlots\n") is None
