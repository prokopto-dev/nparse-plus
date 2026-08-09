"""The on-disk dump library (Qt-free).

One directory per character, one subdirectory per kind, one JSON file per
snapshot::

    <data_dir>/dumps/Prokopton/inventory/20260730-145900-1f4c….json
                              /spellbook/20260616-105000-93ab….json

The filename carries both halves of a snapshot's identity — when the player
took it, and a digest of what it said — so listing the library is a directory
scan with no file reads, and re-importing an unchanged dump lands on the path
that already exists instead of growing a duplicate.

Everything here is best-effort in the same sense as the socials mirror: a
library that cannot be read or written must never disturb the log pipeline.
Failures are logged and reported as "nothing happened", never raised at the
caller.
"""

from __future__ import annotations

import json
import logging
import shutil
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ValidationError

from nparseplus.core.dumps.models import (
    SCHEMA_VERSION,
    CharacterDump,
    DumpKind,
)

logger = logging.getLogger(__name__)

#: How many snapshots of one character+kind to keep by default.
DEFAULT_KEEP = 10

_STAMP_FORMAT = "%Y%m%d-%H%M%S"


class SnapshotRef(BaseModel):
    """A snapshot located but not read — everything the filename encodes."""

    character: str
    kind: DumpKind
    captured_at: datetime
    digest: str
    path: Path

    @property
    def label(self) -> str:
        return self.captured_at.strftime("%Y-%m-%d %H:%M")


def safe_name(name: str) -> str:
    """Filesystem-safe form of a character name (they are alnum in game)."""
    cleaned = "".join(ch for ch in name if ch.isalnum() or ch in "_-")
    return cleaned[:64]


def snapshot_filename(captured_at: datetime, digest: str) -> str:
    return f"{captured_at.strftime(_STAMP_FORMAT)}-{digest}.json"


def parse_snapshot_filename(name: str) -> tuple[datetime, str] | None:
    """``(captured_at, digest)`` from a snapshot filename, or None."""
    stem = name[:-5] if name.endswith(".json") else name
    date_part, _, rest = stem.partition("-")
    time_part, _, digest = rest.partition("-")
    if not digest:
        return None
    try:
        captured_at = datetime.strptime(f"{date_part}-{time_part}", _STAMP_FORMAT)
    except ValueError:
        return None
    return captured_at, digest


class DumpLibrary:
    """Read/write access to the stored snapshots. Thread-safe by accident
    only — treat it as owned by whichever thread the caller runs on, and note
    that the watcher does all of its writing on the driver thread."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    # -- locating ---------------------------------------------------------

    def characters(self) -> list[str]:
        try:
            entries = sorted(self.root.iterdir())
        except OSError:
            return []
        return [entry.name for entry in entries if entry.is_dir()]

    def kinds(self, character: str) -> list[DumpKind]:
        """The kinds this character actually has snapshots for."""
        return [kind for kind in DumpKind if self.snapshots(character, kind)]

    def snapshots(self, character: str, kind: DumpKind) -> list[SnapshotRef]:
        """Every stored snapshot for one character and kind, newest first."""
        directory = self.root / safe_name(character) / str(kind)
        try:
            names = sorted(entry.name for entry in directory.iterdir() if entry.is_file())
        except OSError:
            return []
        refs: list[SnapshotRef] = []
        for name in names:
            parsed = parse_snapshot_filename(name)
            if parsed is None:
                continue
            captured_at, digest = parsed
            refs.append(
                SnapshotRef(
                    character=character,
                    kind=kind,
                    captured_at=captured_at,
                    digest=digest,
                    path=directory / name,
                )
            )
        refs.sort(key=lambda ref: (ref.captured_at, ref.digest), reverse=True)
        return refs

    def latest(self, character: str, kind: DumpKind) -> SnapshotRef | None:
        refs = self.snapshots(character, kind)
        return refs[0] if refs else None

    def total_snapshots(self) -> int:
        return sum(
            len(self.snapshots(character, kind))
            for character in self.characters()
            for kind in DumpKind
        )

    # -- reading ----------------------------------------------------------

    def load(self, ref: SnapshotRef) -> CharacterDump | None:
        """Read one snapshot; None when missing, unreadable, corrupt, or new."""
        return self.load_path(ref.path)

    def load_path(self, path: Path | str) -> CharacterDump | None:
        """Read a snapshot by path — what a bus event carries instead of a ref."""
        try:
            raw = Path(path).read_text(encoding="utf-8")
        except OSError:
            return None
        try:
            data = json.loads(raw)
        except ValueError:
            return None
        if not isinstance(data, dict):
            return None
        if int(data.get("schema_version", SCHEMA_VERSION)) > SCHEMA_VERSION:
            # Written by a newer nParse+. Reading it under the old shape would
            # quietly drop fields; show nothing rather than something wrong.
            return None
        try:
            return CharacterDump.model_validate(data)
        except ValidationError:
            return None

    def load_latest(self, character: str, kind: DumpKind) -> CharacterDump | None:
        ref = self.latest(character, kind)
        return self.load(ref) if ref is not None else None

    # -- writing ----------------------------------------------------------

    def is_duplicate(self, dump: CharacterDump) -> bool:
        """Whether the newest stored snapshot already says exactly this.

        The gate between "the player re-ran /outputfile out of habit" and
        "something changed" — an unchanged dump must not accumulate.
        """
        latest = self.latest(dump.character, dump.kind)
        return latest is not None and latest.digest == dump.digest

    def store(
        self,
        dump: CharacterDump,
        *,
        keep: int = DEFAULT_KEEP,
        now: datetime | None = None,
    ) -> SnapshotRef | None:
        """Persist ``dump`` and prune old snapshots. None if the write failed.

        Idempotent: a snapshot with the same capture time and digest is
        already this dump, so it is left alone.
        """
        directory = self.root / safe_name(dump.character) / str(dump.kind)
        path = directory / snapshot_filename(dump.captured_at, dump.digest)
        ref = SnapshotRef(
            character=dump.character,
            kind=dump.kind,
            captured_at=dump.captured_at,
            digest=dump.digest,
            path=path,
        )
        if path.exists():
            return ref
        stored = dump.model_copy(update={"imported_at": now or datetime.now()})
        try:
            directory.mkdir(parents=True, exist_ok=True)
            tmp_path = path.with_name(path.name + ".tmp")
            tmp_path.write_text(stored.model_dump_json(indent=2), encoding="utf-8")
            tmp_path.replace(path)
        except OSError:
            logger.warning("could not store the %s dump for %s", dump.kind, dump.character)
            return None
        self.prune(dump.character, dump.kind, keep)
        return ref

    def prune(self, character: str, kind: DumpKind, keep: int) -> list[SnapshotRef]:
        """Delete all but the ``keep`` newest snapshots; returns what went."""
        if keep < 1:
            return []
        removed: list[SnapshotRef] = []
        for ref in self.snapshots(character, kind)[keep:]:
            if self.delete(ref):
                removed.append(ref)
        return removed

    def delete(self, ref: SnapshotRef) -> bool:
        try:
            Path(ref.path).unlink()
        except OSError:
            return False
        return True

    def delete_character(self, character: str) -> bool:
        """Forget a character entirely (every kind, every snapshot)."""
        directory = self.root / safe_name(character)
        try:
            shutil.rmtree(directory)
        except OSError:
            return False
        return True
