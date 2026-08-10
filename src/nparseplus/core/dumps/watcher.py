"""Automatic import of ``/outputfile`` dumps into the library (Qt-free).

Follows the ``InventoryWatcher`` / ``SocialSyncWatcher`` idiom: dependencies
injected as zero-arg callables, driven from ``driver.on_tick``, every failure
swallowed — an import problem must never disturb the log pipeline.

Two toggles, because they answer different questions:

``auto_import``
    Pick up dumps for a character+kind the library has never seen. This is
    also the master switch: with it off the watcher does no scanning at all.

``auto_update``
    Store a *new* snapshot when a dump the library already tracks changes.
    Off means the first import of each character+kind is kept and later
    ``/outputfile`` runs are ignored, which is what you want if you took a
    dump deliberately and don't want it displaced by an idle re-run.

Unlike ``InventoryWatcher`` this deliberately does **not** prime its mtimes
at startup. That watcher primes because uploading a stale dump to a website
is a side effect; here the library starts empty and files already sitting in
the EQ directory are exactly what the user wants collected. Re-importing is
harmless anyway: :meth:`DumpLibrary.is_duplicate` drops a dump whose contents
match the newest stored snapshot, so an unchanged file never accumulates.

**All bus publishing happens on the driver thread.** The GUI never imports
anything itself; it calls :meth:`request_scan` / :meth:`request_import`,
which are thread-safe inboxes drained by the next tick — the same shape
``SharingCoordinator`` uses for inbound network traffic, and for the same
reason (the bus is not thread-safe).
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from nparseplus.core.bus import EventBus
from nparseplus.core.dumps.models import CharacterDump, diff_dumps
from nparseplus.core.dumps.parse import KIND_SUFFIXES, read_dump_file
from nparseplus.core.dumps.store import DEFAULT_KEEP, DumpLibrary, SnapshotRef
from nparseplus.core.events import (
    CharacterDumpImportedEvent,
    CharacterDumpUpdatedEvent,
)

logger = logging.getLogger(__name__)

#: Dumps change when a human types a command, so polling hard buys nothing.
SCAN_INTERVAL_SECONDS = 20.0


@dataclass
class ScanResult:
    """What one scan did, for the window's status line and for tests."""

    imported: list[SnapshotRef] = field(default_factory=list)
    updated: list[SnapshotRef] = field(default_factory=list)
    unchanged: int = 0
    #: Changed dumps left alone because ``auto_update`` is off.
    skipped: int = 0
    examined: int = 0

    @property
    def stored(self) -> int:
        return len(self.imported) + len(self.updated)


class DumpWatcher:
    """Folds the EQ directory's dump files into the library."""

    def __init__(
        self,
        library: DumpLibrary,
        *,
        get_eq_dir: Callable[[], Path | None],
        is_enabled: Callable[[], bool],
        is_update_enabled: Callable[[], bool],
        get_keep: Callable[[], int] = lambda: DEFAULT_KEEP,
        bus: EventBus | None = None,
        on_fresh_dump: Callable[[CharacterDump], None] | None = None,
    ) -> None:
        self.library = library
        self._get_eq_dir = get_eq_dir
        self._is_enabled = is_enabled
        self._is_update_enabled = is_update_enabled
        self._get_keep = get_keep
        self._bus = bus
        self._on_fresh_dump = on_fresh_dump
        self._next_scan: datetime | None = None
        self._mtimes: dict[Path, float] = {}
        self._scanned_dir: Path | None = None
        self._updates_were_enabled: bool | None = None
        # GUI -> driver inbox (see the module docstring).
        self._lock = threading.Lock()
        self._scan_requested = False
        self._pending_files: list[tuple[Path, str]] = []
        #: Last scan's outcome, for the window to render.
        self.last_scan_at: datetime | None = None
        self.last_result: ScanResult | None = None

    # -- thread-safe requests from the GUI --------------------------------

    def request_scan(self) -> None:
        """Ask for a full rescan on the next tick, toggles or not.

        A button the user pressed always works: "Import now" would be a
        strange thing to have quietly obey the auto-import checkbox.
        """
        with self._lock:
            self._scan_requested = True

    def request_import(self, path: Path, character: str = "") -> None:
        """Ask for one hand-picked file to be imported on the next tick.

        ``character`` overrides whatever the filename implies — the GUI asks
        the user when a hand-picked file's name says nothing, and that answer
        has to survive the trip to the driver thread.
        """
        with self._lock:
            self._pending_files.append((Path(path), character))

    def _take_requests(self) -> tuple[bool, list[tuple[Path, str]]]:
        with self._lock:
            requested, files = self._scan_requested, self._pending_files
            self._scan_requested, self._pending_files = False, []
        return requested, files

    # -- driver tick ------------------------------------------------------

    def tick(self, now: datetime) -> None:
        """Driver-tick hook. Never raises."""
        try:
            self._tick(now)
        except Exception:  # pragma: no cover - defensive
            logger.debug("dump watcher tick failed", exc_info=True)

    def _tick(self, now: datetime) -> None:
        requested, files = self._take_requests()
        for path, character in files:
            self.import_file(path, now, character=character)

        if requested:
            self._mtimes.clear()  # a manual rescan re-reads everything
            self.scan(now, allow_updates=True)
            return
        if not self._is_enabled():
            # Forget the cadence so enabling mid-session scans promptly.
            self._next_scan = None
            return

        allow_updates = self._is_update_enabled()
        if allow_updates and self._updates_were_enabled is False:
            # Files whose changes we ignored while the toggle was off were
            # still cached as seen; drop the cache so they get another look.
            self._mtimes.clear()
        self._updates_were_enabled = allow_updates

        if self._next_scan is not None and now < self._next_scan:
            return
        self._next_scan = now + timedelta(seconds=SCAN_INTERVAL_SECONDS)
        self.scan(now, allow_updates=allow_updates)

    # -- the work ---------------------------------------------------------

    def scan(self, now: datetime, *, allow_updates: bool) -> ScanResult:
        """Import every changed dump file in the EQ directory."""
        result = ScanResult()
        eq_dir = self._get_eq_dir()
        if eq_dir is None or not Path(eq_dir).is_dir():
            return result
        eq_dir = Path(eq_dir)
        if self._scanned_dir != eq_dir:
            self._mtimes.clear()
            self._scanned_dir = eq_dir

        for path in self._candidates(eq_dir):
            try:
                mtime = path.stat().st_mtime
            except OSError:
                continue
            if self._mtimes.get(path) == mtime:
                continue
            result.examined += 1
            # Remember it either way. A change held back by auto_update does
            # get reconsidered — _tick clears the whole cache when that toggle
            # comes back on, as does request_scan — so leaving it uncached
            # only bought a stat-read-parse of the same file every 20s forever.
            self._mtimes[path] = mtime
            dump = read_dump_file(path)
            if dump is None:
                continue
            self._ingest(dump, now, allow_updates=allow_updates, result=result, automatic=True)

        self.last_scan_at = now
        self.last_result = result
        if result.stored:
            logger.info(
                "dump library: %d imported, %d updated", len(result.imported), len(result.updated)
            )
        return result

    def import_file(
        self, path: Path, now: datetime | None = None, *, character: str = ""
    ) -> SnapshotRef | None:
        """Import one file regardless of the toggles (the hand-picked path).

        ``sniff=True``: the user pointed at this file, so its contents are
        worth reading even when the name gives nothing away. The scan above
        never sniffs — it must not open every .txt in the EQ directory.

        ``automatic=False``: a file the user browsed to gets filed away, never
        published. It may be a backup, an export off another machine, or
        another player's dump entirely — none of which anyone asked to send
        to a website by picking it out of a file dialog.
        """
        dump = read_dump_file(Path(path), character=character, sniff=True)
        if dump is None:
            return None
        result = ScanResult()
        self._ingest(
            dump, now or datetime.now(), allow_updates=True, result=result, automatic=False
        )
        stored = result.imported + result.updated
        return stored[0] if stored else None

    def _candidates(self, eq_dir: Path) -> list[Path]:
        """Dump-looking files, by name — the EQ directory holds hundreds of
        unrelated .txt files and none of them deserve a read."""
        try:
            entries = sorted(eq_dir.glob("*.txt"))
        except OSError:
            return []
        return [
            path for path in entries if any(path.stem.lower().endswith(s) for s in KIND_SUFFIXES)
        ]

    def _ingest(
        self,
        dump: CharacterDump,
        now: datetime,
        *,
        allow_updates: bool,
        result: ScanResult,
        automatic: bool,
    ) -> None:
        """Store one parsed dump, tallying what happened into ``result``.

        ``automatic`` says this dump came from the directory poll rather than
        from a file the user hand-picked, which is what decides whether the
        fresh-dump hook fires. See :meth:`_notify_fresh`.
        """
        previous = self.library.latest(dump.character, dump.kind)
        if previous is not None and previous.digest == dump.digest:
            result.unchanged += 1
            return

        # Fresh content. Tell the hook BEFORE the retention decision below:
        # `auto_update` chooses how much local history to keep and has no
        # business deciding what leaves the machine. Gating the hook on it
        # meant one stale snapshot at startup could silence every upload for
        # the rest of the session.
        if automatic:
            self._notify_fresh(dump)

        if previous is not None and not allow_updates:
            result.skipped += 1
            return

        before = self.library.load(previous) if previous is not None else None
        ref = self.library.store(dump, keep=self._get_keep(), now=now)
        if ref is None:
            return  # a write failure; retrying every tick helps nobody
        if previous is None:
            result.imported.append(ref)
            self._publish_imported(dump, ref)
        else:
            result.updated.append(ref)
            self._publish_updated(dump, ref, before)

    def _notify_fresh(self, dump: CharacterDump) -> None:
        """A dump the automatic scan just saw change, whether or not it was kept.

        Deliberately NOT one of the bus events. Those say "the library stored
        a snapshot", which is a fact about local history — a plugin reading
        them wants exactly that. The uploader needs a different fact: "the
        game just wrote a fresh dump in the EQ directory". Routing uploads
        through the persistence events conflated the two and broke both
        directions — a retention setting suppressed uploads, and importing a
        file by hand (a backup, or somebody else's dump) published it.

        Never fires for a hand-picked import: the user asked to file that
        away, not to publish it. The Upload inventory button is how you ask
        for that, and it says so.
        """
        if self._on_fresh_dump is None:
            return
        try:
            self._on_fresh_dump(dump)
        except Exception:  # pragma: no cover - defensive
            logger.warning("the fresh-dump hook failed", exc_info=True)

    # -- events (the plugin-facing hooks) ---------------------------------

    def _event_fields(self, dump: CharacterDump, ref: SnapshotRef) -> dict[str, object]:
        return {
            "character": dump.character,
            "kind": str(dump.kind),
            "server": dump.server,
            "captured_at": dump.captured_at,
            "entry_count": dump.entry_count,
            "digest": dump.digest,
            "path": str(ref.path),
            "source_file": dump.source_file,
        }

    def _publish_imported(self, dump: CharacterDump, ref: SnapshotRef) -> None:
        if self._bus is None:
            return
        self._bus.publish(CharacterDumpImportedEvent(**self._event_fields(dump, ref)))

    def _publish_updated(
        self, dump: CharacterDump, ref: SnapshotRef, before: CharacterDump | None
    ) -> None:
        if self._bus is None:
            return
        change = diff_dumps(before, dump)
        self._bus.publish(
            CharacterDumpUpdatedEvent(
                **self._event_fields(dump, ref),
                added=tuple(change.added),
                removed=tuple(change.removed),
            )
        )

    # -- status -----------------------------------------------------------

    def status_text(self) -> str:
        """One line for the dump window's status strip."""
        total = self.library.total_snapshots()
        held = f"{total} snapshot{'s' if total != 1 else ''} stored"
        result = self.last_result
        if self.last_scan_at is None or result is None:
            return f"{held}. Not scanned yet this session."
        stamp = self.last_scan_at.strftime("%H:%M")
        parts = []
        if result.imported:
            parts.append(f"{len(result.imported)} imported")
        if result.updated:
            parts.append(f"{len(result.updated)} updated")
        if result.skipped:
            parts.append(f"{result.skipped} changed but auto-update is off")
        detail = ", ".join(parts) if parts else "no changes"
        return f"{held}. Last scan {stamp} — {detail}."
