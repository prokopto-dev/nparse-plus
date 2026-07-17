"""Inventory dump watcher (Qt-free).

Port of EQTool's Services/InventoryWatcherService.cs: the game's
``/outputfile inventory`` command writes a TSV (``Location Name ID Count
Slots``) into the EQ directory; when one appears or changes it is parsed
and uploaded (Bearer ``api_token``) to pigparse.org's character browser.

Divergences from the C#:
- Polling (the LogDriver idiom) instead of a FileSystemWatcher; existing
  files are primed at startup so only fresh dumps upload.
- Gated by an explicit ``inventory_upload`` opt-in as well as the token.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import IntEnum
from pathlib import Path

from nparseplus.core.pigparse import PigParseApi, SubmitFn
from nparseplus.core.player import ActivePlayer

SCAN_INTERVAL_SECONDS = 2.0
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


class InventoryWatcher:
    """Driver-tick poller: fresh inventory dumps -> pigparse upload."""

    def __init__(
        self,
        player: ActivePlayer,
        *,
        get_eq_dir: Callable[[], Path | None],
        is_enabled: Callable[[], bool],
        get_token: Callable[[], str],
        api: PigParseApi | None = None,
        submit: SubmitFn | None = None,
    ) -> None:
        self.player = player
        self._get_eq_dir = get_eq_dir
        self._is_enabled = is_enabled
        self._get_token = get_token
        self.api = api
        self.submit = submit
        self._mtimes: dict[Path, float] = {}
        self._primed = False
        self._last_scan: datetime | None = None

    def _scan(self, eq_dir: Path) -> list[Path]:
        changed: list[Path] = []
        try:
            candidates = list(eq_dir.glob("*.txt"))
        except OSError:
            return changed
        for path in candidates:
            try:
                mtime = path.stat().st_mtime
            except OSError:
                continue
            if self._mtimes.get(path) != mtime:
                self._mtimes[path] = mtime
                changed.append(path)
        return changed

    def tick(self, now: datetime) -> None:
        if (
            self._last_scan is not None
            and (now - self._last_scan).total_seconds() < SCAN_INTERVAL_SECONDS
        ):
            return
        self._last_scan = now

        eq_dir = self._get_eq_dir()
        if eq_dir is None or not eq_dir.is_dir():
            return
        changed = self._scan(eq_dir)
        if not self._primed:
            # First pass just records what already exists — a dump from a
            # previous session must not upload at startup.
            self._primed = True
            return

        api, submit, token = self.api, self.submit, self._get_token()
        character, server = self.player.name, self.player.server
        if (
            not changed
            or api is None
            or submit is None
            or not self._is_enabled()
            or not token
            or not character
            or server is None
        ):
            return

        server_int = int(server)
        for path in changed:
            try:
                items = parse_inventory_text(path.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                continue
            if not items:
                continue

            def fetch(_items: list[InventoryItem] = items) -> None:
                api.upload_inventory(
                    character_name=character,
                    server=server_int,
                    items=_items,
                    api_token=token,
                )

            submit(fetch, None)
