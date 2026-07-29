"""Per-character mirror of the socials we have seen and written (Qt-free).

The client's ``.ini`` stays the source of truth — this is a shadow copy kept
under the app's data directory. It exists for three things the ini alone
cannot give us:

* **Clobber detection.** The client rewrites the whole character ini when you
  camp or log out, so macros can silently vanish. The mirror still has them.
* **Provenance.** Which macros came from the game, which were authored in the
  Macro Editor, and which arrived in an imported pack.
* **Restore.** A manual "put back what the client dropped" path.

Attribution rests on :attr:`SocialRecord.written_digest` — a digest of the
value *we* last wrote to that slot. On the next read, a slot still matching
its digest is one nobody has touched since, so its origin stands; a slot whose
value differs was changed by the client or by the player in game, so it flips
to :attr:`SocialOrigin.GAME`. This is a heuristic, not a fact: a player who
edits a macro in game *back to* the value we wrote keeps the old label. That
is why the UI says "last written by" rather than "authored by".

Everything here is best-effort. A store that cannot be read or written must
never block an ini write — the mirror is a convenience, and deleting the whole
directory simply makes every macro read as ``GAME`` again.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Sequence
from datetime import datetime
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from nparseplus.core.socials import Social, SocialGrid, normalize_socials

SCHEMA_VERSION = 1


class SocialOrigin(StrEnum):
    GAME = "game"
    LOCAL = "local"
    IMPORTED = "imported"


class SocialRecord(BaseModel):
    model_config = ConfigDict(extra="ignore")

    social: Social
    origin: SocialOrigin = SocialOrigin.GAME
    source_label: str = ""
    first_seen: datetime
    updated_at: datetime
    written_digest: str = ""
    in_file: bool = True

    @property
    def slot(self) -> tuple[int, int]:
        return self.social.slot


class SocialStore(BaseModel):
    model_config = ConfigDict(extra="ignore")

    character: str = ""
    server: str = ""
    schema_version: int = SCHEMA_VERSION
    updated_at: datetime | None = None
    records: list[SocialRecord] = Field(default_factory=list)
    snapshot: list[str] = Field(default_factory=list)

    def at(self, page: int, button: int) -> SocialRecord | None:
        for record in self.records:
            if record.slot == (page, button):
                return record
        return None

    def origin_at(self, page: int, button: int) -> SocialOrigin:
        record = self.at(page, button)
        return record.origin if record is not None else SocialOrigin.GAME

    def lost(self) -> list[SocialRecord]:
        """Records we hold that are no longer present in the character's file."""
        return [record for record in self.records if not record.in_file]


class SyncReport(BaseModel):
    added: list[tuple[int, int]] = Field(default_factory=list)
    changed: list[tuple[int, int]] = Field(default_factory=list)
    lost: list[tuple[int, int]] = Field(default_factory=list)
    unchanged: list[tuple[int, int]] = Field(default_factory=list)


def digest(social: Social) -> str:
    """Stable digest of a social's meaningful content."""
    payload = json.dumps(
        [social.name.strip(), social.color, [line.strip() for line in social.lines]],
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def store_path(root: Path, character: str, suffix: str) -> Path:
    """Where one character's mirror lives under ``root``."""
    safe = "".join(ch for ch in f"{character}_{suffix}" if ch.isalnum() or ch in "_-")
    return Path(root) / f"{safe or 'unnamed'}.json"


def load_store(path: Path) -> SocialStore | None:
    """Read a mirror; None when missing, unreadable, corrupt, or too new."""
    try:
        raw = Path(path).read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    if int(data.get("schema_version", SCHEMA_VERSION)) > SCHEMA_VERSION:
        # Written by a newer nParse+; reading it under the old shape would
        # mislabel origins. Start fresh rather than guess.
        return None
    try:
        return SocialStore.model_validate(data)
    except ValidationError:
        return None


def save_store(path: Path, store: SocialStore) -> None:
    """Write a mirror atomically (tmp file + replace), like ``save_settings``."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(path.name + ".tmp")
    tmp_path.write_text(store.model_dump_json(indent=2), encoding="utf-8")
    tmp_path.replace(path)


def new_store(character: str, server: str, *, now: datetime) -> SocialStore:
    return SocialStore(character=character, server=server, updated_at=now)


def sync_from_game(store: SocialStore, grid: SocialGrid, *, now: datetime) -> SyncReport:
    """Reconcile ``store`` against the character's current file. Mutates ``store``.

    A slot whose value still matches ``written_digest`` keeps its recorded
    origin; one that differs flips to ``GAME``. Slots that vanished from the
    file are kept (flagged ``in_file=False``) so they can be restored.
    """
    report = SyncReport()
    by_slot = {record.slot: record for record in store.records}
    seen: set[tuple[int, int]] = set()

    for social in grid.socials:
        seen.add(social.slot)
        current = digest(social)
        record = by_slot.get(social.slot)
        if record is None:
            store.records.append(
                SocialRecord(
                    social=social,
                    origin=SocialOrigin.GAME,
                    first_seen=now,
                    updated_at=now,
                    written_digest="",
                    in_file=True,
                )
            )
            report.added.append(social.slot)
            continue

        was_missing = not record.in_file
        record.in_file = True
        if record.written_digest and record.written_digest == current:
            record.social = social
            report.unchanged.append(social.slot)
            continue
        if digest(record.social) == current and not was_missing:
            report.unchanged.append(social.slot)
            continue
        record.social = social
        record.origin = SocialOrigin.GAME
        record.source_label = ""
        record.written_digest = ""
        record.updated_at = now
        report.changed.append(social.slot)

    for record in store.records:
        if record.slot in seen:
            continue
        if record.in_file:
            record.in_file = False
            record.updated_at = now
        report.lost.append(record.slot)

    store.records.sort(key=lambda record: record.slot)
    store.snapshot = [f"{key}={value}" for key, value in _snapshot_pairs(grid)]
    store.updated_at = now
    return report


def _snapshot_pairs(grid: SocialGrid) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for social in grid.socials:
        prefix = f"Page{social.page}Button{social.button}"
        pairs.append((f"{prefix}Name", social.name))
        pairs.append((f"{prefix}Color", str(social.color)))
        pairs.extend(
            (f"{prefix}Line{index}", line) for index, line in enumerate(social.lines, start=1)
        )
    return pairs


def mark_written(
    store: SocialStore,
    socials: Iterable[Social],
    *,
    origin: SocialOrigin,
    now: datetime,
    source_label: str = "",
    origins: dict[tuple[int, int], SocialOrigin] | None = None,
    source_labels: dict[tuple[int, int], str] | None = None,
) -> None:
    """Record that we just wrote ``socials`` to the character's file.

    ``origins`` and ``source_labels`` override per slot, so one save can mark
    hand-edited slots ``LOCAL`` while slots that arrived in a pack stay
    ``IMPORTED`` and keep the name of the pack they came from.

    A slot with no override **keeps the origin it already had**. This records
    that we wrote the file, not that we authored every macro in it — a save
    with nothing edited must not relabel the whole grid, or one no-op save
    would erase the provenance of everything.
    """
    by_slot = {record.slot: record for record in store.records}
    for social in normalize_socials(socials):
        record = by_slot.get(social.slot)
        overrides = origins or {}
        label_overrides = source_labels or {}
        if record is None:
            store.records.append(
                SocialRecord(
                    social=social,
                    origin=overrides.get(social.slot, origin),
                    source_label=label_overrides.get(social.slot, source_label),
                    first_seen=now,
                    updated_at=now,
                    written_digest=digest(social),
                    in_file=True,
                )
            )
            continue
        if social.slot in overrides:
            record.origin = overrides[social.slot]
        if social.slot in label_overrides:
            record.source_label = label_overrides[social.slot]
        elif social.slot in overrides:
            record.source_label = source_label
        record.social = social
        record.updated_at = now
        record.written_digest = digest(social)
        record.in_file = True
    store.records.sort(key=lambda record: record.slot)
    store.updated_at = now


def forget_slots(store: SocialStore, slots: Sequence[tuple[int, int]], *, now: datetime) -> None:
    """Drop records for slots the user deliberately cleared."""
    wanted = set(slots)
    store.records = [record for record in store.records if record.slot not in wanted]
    store.updated_at = now


def restore_payload(store: SocialStore) -> list[Social]:
    """The socials "Restore from local copy" would write back."""
    return normalize_socials(record.social for record in store.records)
