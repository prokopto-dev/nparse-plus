"""InventoryUploadHandler — pigparse upload driven by the dump library.

The EQ directory is polled once (by ``DumpWatcher``); this handler subscribes
to what that publishes. These tests drive the real watcher rather than
synthesising events, so the two halves are checked joined up.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from nparseplus.core.bus import EventBus
from nparseplus.core.dumps import DumpLibrary, DumpWatcher
from nparseplus.core.enums import Server
from nparseplus.core.handlers.inventory_upload import InventoryUploadHandler
from nparseplus.core.player import ActivePlayer
from nparseplus.net.worker import ImmediateWorker

T0 = datetime(2026, 8, 9, 12, 0, 0)

DUMP = (
    "Location\tName\tID\tCount\tSlots\n"
    "Charm\tGuise of the Deceiver\t1234\t1\t0\n"
    "General1-Slot1\tRusty Sword\t5678\t1\t0\n"
)
CHANGED_DUMP = DUMP.replace("Rusty Sword", "Shiny Sword")
SPELLBOOK = "51\tSuperior Healing\n14\tSpirit of Wolf\n"


class FakeApi:
    def __init__(self) -> None:
        self.uploads: list[dict] = []

    def upload_inventory(self, **kwargs) -> None:
        self.uploads.append(kwargs)


def write(directory: Path, name: str, text: str, when: datetime) -> Path:
    path = directory / name
    path.write_text(text, encoding="utf-8")
    os.utime(path, (when.timestamp(), when.timestamp()))
    return path


class Env:
    def __init__(self, tmp_path: Path, **overrides) -> None:
        self.eq_dir = tmp_path / "eq"
        self.eq_dir.mkdir(parents=True)
        self.api = FakeApi()
        self.bus = EventBus()
        self.state = {"enabled": True, "token": "tok"}
        self.library = DumpLibrary(tmp_path / "library")
        self.player = ActivePlayer()
        self.player.reset_for("Xantik", Server.GREEN)
        kwargs = dict(
            is_enabled=lambda: self.state["enabled"],
            get_token=lambda: self.state["token"],
            session_start=T0,
            api=self.api,
            submit=ImmediateWorker().submit,
        )
        kwargs.update(overrides)
        self.handler = InventoryUploadHandler(self.bus, self.player, self.library, **kwargs)
        self.watcher = DumpWatcher(
            self.library,
            get_eq_dir=lambda: self.eq_dir,
            is_enabled=lambda: True,
            is_update_enabled=lambda: True,
            bus=self.bus,
        )

    def dump(self, name: str, text: str, when: datetime) -> Path:
        return write(self.eq_dir, name, text, when)


@pytest.fixture
def env(tmp_path: Path) -> Env:
    return Env(tmp_path)


def test_a_dump_taken_this_session_uploads(env: Env) -> None:
    env.dump("Xantik-Inventory.txt", DUMP, T0 + timedelta(minutes=1))
    env.watcher.tick(T0 + timedelta(minutes=1))

    assert len(env.api.uploads) == 1
    upload = env.api.uploads[0]
    assert upload["character_name"] == "Xantik"
    assert upload["server"] == int(Server.GREEN)
    assert upload["api_token"] == "tok"
    assert [item.name for item in upload["items"]] == ["Guise of the Deceiver", "Rusty Sword"]


def test_a_dump_from_before_this_session_does_not_upload(env: Env) -> None:
    """Replaces the old watcher's startup priming, keyed on capture time."""
    env.dump("Xantik-Inventory.txt", DUMP, T0 - timedelta(days=1))
    env.watcher.tick(T0)

    assert env.api.uploads == []
    # ...but it is still collected into the library, which is the point.
    assert env.library.characters() == ["Xantik"]


def test_rerunning_outputfile_unchanged_does_not_reupload(env: Env) -> None:
    env.dump("Xantik-Inventory.txt", DUMP, T0 + timedelta(minutes=1))
    env.watcher.tick(T0 + timedelta(minutes=1))
    env.dump("Xantik-Inventory.txt", DUMP, T0 + timedelta(minutes=5))
    env.watcher.tick(T0 + timedelta(minutes=5))
    assert len(env.api.uploads) == 1


def test_a_changed_dump_uploads_again(env: Env) -> None:
    env.dump("Xantik-Inventory.txt", DUMP, T0 + timedelta(minutes=1))
    env.watcher.tick(T0 + timedelta(minutes=1))
    env.dump("Xantik-Inventory.txt", CHANGED_DUMP, T0 + timedelta(minutes=5))
    env.watcher.tick(T0 + timedelta(minutes=5))

    assert len(env.api.uploads) == 2
    assert [item.name for item in env.api.uploads[1]["items"]][-1] == "Shiny Sword"


def test_spellbook_dumps_are_not_uploaded(env: Env) -> None:
    env.dump("Xantik-Spellbook.txt", SPELLBOOK, T0 + timedelta(minutes=1))
    env.watcher.tick(T0 + timedelta(minutes=1))
    assert env.api.uploads == []


def test_gated_on_the_toggle_and_the_token(tmp_path: Path) -> None:
    env = Env(tmp_path)
    env.state["enabled"] = False
    env.dump("Xantik-Inventory.txt", DUMP, T0 + timedelta(minutes=1))
    env.watcher.tick(T0 + timedelta(minutes=1))
    assert env.api.uploads == []

    other = Env(tmp_path / "b")
    other.state["token"] = ""
    other.dump("Xantik-Inventory.txt", DUMP, T0 + timedelta(minutes=1))
    other.watcher.tick(T0 + timedelta(minutes=1))
    assert other.api.uploads == []


def test_the_dump_file_names_the_character(tmp_path: Path) -> None:
    """A deliberate divergence from the C#, which uploads under whoever is
    logged in — the filename knows whose inventory this actually is."""
    env = Env(tmp_path)
    env.dump("Beeta-Inventory.txt", DUMP, T0 + timedelta(minutes=1))
    env.watcher.tick(T0 + timedelta(minutes=1))
    assert env.api.uploads[0]["character_name"] == "Beeta"
    assert env.api.uploads[0]["server"] == int(Server.GREEN)  # still the live server


def test_no_api_or_no_server_is_a_no_op(tmp_path: Path) -> None:
    env = Env(tmp_path, api=None)
    env.dump("Xantik-Inventory.txt", DUMP, T0 + timedelta(minutes=1))
    env.watcher.tick(T0 + timedelta(minutes=1))
    assert env.api.uploads == []

    logged_out = Env(tmp_path / "b")
    logged_out.player.reset_for("", None)
    logged_out.dump("Xantik-Inventory.txt", DUMP, T0 + timedelta(minutes=1))
    logged_out.watcher.tick(T0 + timedelta(minutes=1))
    assert logged_out.api.uploads == []
