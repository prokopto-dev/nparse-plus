"""Automatic capture of in-game macro changes into the local mirror (Qt-free).

Macros made or edited *in game* are invisible to nParse+ until someone opens
the Macro Editor and clicks Load. This watcher closes that gap: when the EQ
client exits, it re-reads every character ini on the configured server and
folds the result into each character's :mod:`~nparseplus.core.socialstore`
mirror, so new macros are captured with correct provenance and anything the
client dropped is recorded as recoverable.

**Read-only, by design.** It reads character inis and writes only nParse+'s
own mirror files — it never writes into the EQ install directory. Restoring a
clobbered macro stays a deliberate click in the Macro Editor, because
rewriting game files unattended is not something an overlay should do.

It syncs on the client-exit *edge* (running → not running) rather than on a
timer, so it never races the client while it holds the files open, and it
sees the state the client left behind. A first tick that finds the game
already closed also syncs once, so enabling the setting mid-session works
without waiting for a launch/quit cycle. Mtimes gate the actual work, so a
character whose file has not changed costs one ``stat``.

Follows the ``InventoryWatcher`` idiom: everything injected as zero-arg
callables, driven from ``driver.on_tick``, and every failure swallowed —
a sync problem must never disturb the log pipeline.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

from nparseplus.core import eqini, socialstore
from nparseplus.core import socials as socials_core
from nparseplus.core.eqprocess import eq_is_running

logger = logging.getLogger(__name__)

SCAN_INTERVAL_SECONDS = 15.0


class SocialSyncWatcher:
    """Folds in-game macro changes into the local mirror when EQ exits."""

    def __init__(
        self,
        *,
        get_eq_dir: Callable[[], Path | None],
        get_store_dir: Callable[[], Path | None],
        is_enabled: Callable[[], bool],
        get_servers: Callable[[], list[str]] | None = None,
        is_running: Callable[[], bool] = eq_is_running,
    ) -> None:
        self._get_eq_dir = get_eq_dir
        self._get_store_dir = get_store_dir
        self._is_enabled = is_enabled
        self._get_servers = get_servers or (lambda: list(eqini.SERVER_SUFFIXES.values()))
        self._is_running = is_running
        self._next_scan: datetime | None = None
        self._was_running: bool | None = None
        self._mtimes: dict[Path, float] = {}
        #: Counts of what the last sync did, for the settings window to show.
        self.last_synced_at: datetime | None = None
        self.last_added = 0
        self.last_changed = 0
        self.last_lost = 0

    def tick(self, now: datetime) -> None:
        """Driver-tick hook. Never raises."""
        try:
            self._tick(now)
        except Exception:  # pragma: no cover - defensive
            logger.debug("socials autosync tick failed", exc_info=True)

    def _tick(self, now: datetime) -> None:
        if not self._is_enabled():
            # Forget the edge so re-enabling mid-session syncs promptly.
            self._was_running = None
            return
        if self._next_scan is not None and now < self._next_scan:
            return
        self._next_scan = now + timedelta(seconds=SCAN_INTERVAL_SECONDS)

        running = self._is_running()
        previously = self._was_running
        self._was_running = running
        if running:
            return
        # Sync on the running -> stopped edge, and once when we start up with
        # the game already closed (previously is None).
        if previously is False:
            return
        self.sync(now)

    def sync(self, now: datetime) -> int:
        """Fold every character's current socials into their mirror.

        Returns the number of character files examined. Reads game files and
        writes only nParse+'s own mirrors.
        """
        eq_dir = self._get_eq_dir()
        store_dir = self._get_store_dir()
        if eq_dir is None or store_dir is None or eqini.preflight(eq_dir) is not None:
            return 0

        added = changed = lost = 0
        examined = 0
        for suffix in self._get_servers():
            for path in eqini.character_ini_files(eq_dir, suffix):
                try:
                    mtime = path.stat().st_mtime
                except OSError:
                    continue
                if self._mtimes.get(path) == mtime:
                    continue
                self._mtimes[path] = mtime
                examined += 1

                character = eqini.character_name(path, suffix)
                store_path = socialstore.store_path(store_dir, character, suffix)
                store = socialstore.load_store(store_path) or socialstore.new_store(
                    character, suffix, now=now
                )
                report = socialstore.sync_from_game(store, socials_core.read_socials(path), now=now)
                added += len(report.added)
                changed += len(report.changed)
                lost += len(report.lost)
                try:
                    socialstore.save_store(store_path, store)
                except OSError:
                    logger.debug("could not update the socials mirror for %s", character)

        if examined:
            self.last_synced_at = now
            self.last_added, self.last_changed, self.last_lost = added, changed, lost
            logger.info(
                "socials autosync: %d file(s), %d new, %d changed, %d missing",
                examined,
                added,
                changed,
                lost,
            )
        return examined

    def status_text(self) -> str:
        """One line for the settings window."""
        if self.last_synced_at is None:
            return "Not synced yet this session."
        stamp = self.last_synced_at.strftime("%H:%M")
        parts = []
        if self.last_added:
            parts.append(f"{self.last_added} new")
        if self.last_changed:
            parts.append(f"{self.last_changed} changed")
        if self.last_lost:
            parts.append(f"{self.last_lost} missing from the game files")
        detail = ", ".join(parts) if parts else "no changes"
        return f"Last synced at {stamp} — {detail}."
