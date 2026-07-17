"""core.inventory — dump parsing + poll watcher (InventoryWatcherService port)."""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from pathlib import Path

from nparseplus.core.enums import Server
from nparseplus.core.inventory import (
    InventoryLocation,
    InventoryWatcher,
    parse_inventory_text,
)
from nparseplus.core.player import ActivePlayer
from nparseplus.net.worker import ImmediateWorker

T0 = datetime(2026, 7, 8, 12, 0, 0)

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


class FakeApi:
    def __init__(self) -> None:
        self.uploads: list[dict] = []

    def upload_inventory(self, **kwargs) -> None:
        self.uploads.append(kwargs)


def _watcher(tmp_path: Path, api: FakeApi, **overrides) -> InventoryWatcher:
    player = ActivePlayer()
    player.reset_for("Xantik", Server.GREEN)
    kwargs = dict(
        get_eq_dir=lambda: tmp_path,
        is_enabled=lambda: True,
        get_token=lambda: "tok",
        api=api,
        submit=ImmediateWorker().submit,
    )
    kwargs.update(overrides)
    return InventoryWatcher(player, **kwargs)


def _touch(path: Path, offset: float) -> None:
    stamp = path.stat().st_mtime + offset
    os.utime(path, (stamp, stamp))


def test_watcher_uploads_fresh_dump_only(tmp_path: Path) -> None:
    stale = tmp_path / "Xantik-Inventory.txt"
    stale.write_text(DUMP)
    api = FakeApi()
    watcher = _watcher(tmp_path, api)

    watcher.tick(T0)  # priming pass: pre-existing dump must not upload
    assert api.uploads == []

    _touch(stale, 5)  # the game rewrites the dump
    watcher.tick(T0 + timedelta(seconds=3))
    assert len(api.uploads) == 1
    upload = api.uploads[0]
    assert upload["character_name"] == "Xantik"
    assert upload["server"] == int(Server.GREEN)
    assert upload["api_token"] == "tok"
    assert next(i.name for i in upload["items"]) == "Guise of the Deceiver"

    # Unchanged file: nothing more.
    watcher.tick(T0 + timedelta(seconds=6))
    assert len(api.uploads) == 1


def test_watcher_ignores_non_inventory_txt(tmp_path: Path) -> None:
    api = FakeApi()
    watcher = _watcher(tmp_path, api)
    watcher.tick(T0)
    log = tmp_path / "eqlog_Xantik_P1999Green.txt"
    log.write_text("[Wed Jul 15 12:00:00 2026] You begin casting Clarity.\n")
    watcher.tick(T0 + timedelta(seconds=3))
    assert api.uploads == []


def test_watcher_gates_on_toggle_and_token(tmp_path: Path) -> None:
    dump = tmp_path / "Xantik-Inventory.txt"
    api = FakeApi()
    disabled = _watcher(tmp_path, api, is_enabled=lambda: False)
    disabled.tick(T0)
    dump.write_text(DUMP)
    disabled.tick(T0 + timedelta(seconds=3))
    assert api.uploads == []

    no_token = _watcher(tmp_path, api, get_token=lambda: "")
    no_token.tick(T0 + timedelta(seconds=6))
    _touch(dump, 5)
    no_token.tick(T0 + timedelta(seconds=9))
    assert api.uploads == []


def test_watcher_scan_interval(tmp_path: Path) -> None:
    dump = tmp_path / "Xantik-Inventory.txt"
    dump.write_text(DUMP)
    api = FakeApi()
    watcher = _watcher(tmp_path, api)
    watcher.tick(T0)
    _touch(dump, 5)
    watcher.tick(T0 + timedelta(seconds=1))  # under the 2s scan interval
    assert api.uploads == []
    watcher.tick(T0 + timedelta(seconds=3))
    assert len(api.uploads) == 1
