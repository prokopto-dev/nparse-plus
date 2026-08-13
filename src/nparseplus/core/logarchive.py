"""Log archiving — port of EQTool's LogArchiveService.

When enabled, any ``*.txt`` in the log directory over the size threshold is
copied into ``<log_dir>/archive/<name>_<timestamp>.txt`` and then **truncated
in place**, leaving an empty file behind.

**Not a rename** — that was the bug in #87. The C# moves the file and relies
on Windows refusing to move one the client holds open, which is how the
active log survived the sweep. POSIX has no such notion: the rename succeeds,
EQ (under Wine) keeps writing through its open descriptor to the moved inode,
and no ``eqlog_*.txt`` is left for us to attach to. The app went deaf for the
rest of the session — no timers, no triggers, no dots — with nothing logged,
and restarting nParse+ did not help while the game still held the moved file.
The sweep is *guaranteed* to hit the active log, because the active log is by
definition the one that grows.

Copy-and-truncate is what log rotators do for exactly this reason. The
client's descriptor stays valid; Win32 append writes resolve to end-of-file
at write time (Wine included), so it continues at offset 0. On Windows the
``r+b`` open fails while the client holds the file, so the sweep skips it
with nothing copied — the same outcome as before, and the reason the open
comes first.

**The work is split across two threads on purpose.** Copying a 100 MB log
(80 ms measured, and it scales) does not belong on the thread that tails the
log, so ``stage_oversized_logs`` runs on a ``core.background`` job. But
emptying the log is what the tail has to notice, and no *detection* can be
made airtight: an emptied log the client refills to the tail's read offset,
or past it, before the next 100 ms poll is neither smaller nor — with EQ's
repeated identical lines — different in content at that offset. So
``finish_archive`` runs on the DRIVER thread, from the tick, and calls
``on_rotated`` right after the truncate: the log is emptied and the tail is
reset in one step that no poll can land inside. The tail's own shrink and
signature checks stay as the backstop for rotations nobody tells us about
(the client recreating its log, a user emptying it by hand).
"""

from __future__ import annotations

import contextlib
import logging
import os
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from nparseplus.core.background import BackgroundJob, Spawn

logger = logging.getLogger(__name__)

CHECK_INTERVAL_S = 60 * 60  # hourly, like EQTool
# How many times to go back for lines the client appended while we copied.
# Each pass is smaller than the last, so a couple converge; the bound is
# there so a firehose cannot hold the swap open indefinitely.
CATCHUP_PASSES = 3


@dataclass(frozen=True)
class StagedArchive:
    """A log copied aside, waiting for its swap on the driver thread."""

    source: Path
    partial: Path
    dest: Path
    #: Bytes of ``source`` already in ``partial``; the rest is caught up
    #: during the swap, so the client keeps writing throughout the copy.
    copied: int
    atime_ns: int
    mtime_ns: int


def _copy_aside(path: Path, partial: Path) -> int:
    """Copy ``path`` into ``partial`` durably; returns the bytes copied.

    Opens for write even though it only reads: that is the check that we
    could truncate later, and it is what the client's share mode refuses on
    Windows. Failing here means the file is skipped with nothing copied,
    rather than a duplicate archive appearing every hour.
    """
    with path.open("r+b") as src, partial.open("wb") as out:
        shutil.copyfileobj(src, out)
        out.flush()
        os.fsync(out.fileno())
        return src.tell()


def stage_oversized_logs(log_dir: Path, threshold_mb: int) -> list[StagedArchive]:
    """Copy every oversized log aside. Leaves the originals untouched."""
    if threshold_mb <= 0 or not log_dir.is_dir():
        return []
    threshold = threshold_mb * 1024 * 1024
    archive_dir = log_dir / "archive"
    staged: list[StagedArchive] = []
    for path in log_dir.glob("*.txt"):
        try:
            stat = path.stat()
            if stat.st_size < threshold:
                continue
            archive_dir.mkdir(exist_ok=True)
            stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            dest = archive_dir / f"{path.stem}_{stamp}.txt"
            # A partial name until the copy is complete, so a crash mid-sweep
            # cannot leave a short archive looking like the whole log. It is
            # not a *.txt, so a later sweep will not pick it up either.
            partial = dest.with_name(f".{dest.name}.part")
            copied = _copy_aside(path, partial)
            staged.append(
                StagedArchive(
                    source=path,
                    partial=partial,
                    dest=dest,
                    copied=copied,
                    atime_ns=stat.st_atime_ns,
                    mtime_ns=stat.st_mtime_ns,
                )
            )
        except OSError:
            logger.debug("could not archive %s (in use?)", path, exc_info=True)
    return staged


def finish_archive(
    staged: StagedArchive,
    on_rotated: Callable[[Path], None] | None = None,
) -> Path | None:
    """Seal the copy and empty the log; returns the archive path.

    **Driver thread only.** ``on_rotated`` is called immediately after the
    truncate so that emptying the log and resetting whoever tails it are one
    step — see the module docstring for why detection alone cannot be.
    """
    try:
        with staged.source.open("r+b") as src:
            src.seek(staged.copied)
            with staged.partial.open("ab") as out:
                # The client kept writing while we copied. Whatever it appends
                # after this last read is lost to the truncate below, so go
                # back for it: that shrinks the hole from the length of the
                # copy to the length of one small tail read.
                for _ in range(CATCHUP_PASSES):
                    tail = src.read()
                    if not tail:
                        break
                    out.write(tail)
                out.flush()
                os.fsync(out.fileno())
            staged.partial.replace(staged.dest)
            src.truncate(0)
    except OSError:
        logger.debug("could not archive %s (in use?)", staged.source, exc_info=True)
        with contextlib.suppress(OSError):
            staged.partial.unlink(missing_ok=True)
        return None
    if on_rotated is not None:
        on_rotated(staged.source)
    # An emptied log keeps its old mtime: `logfile.find_active_log` picks the
    # most recently modified log, and a stale character's truncated file would
    # otherwise look like the newest one and pull the driver off the live log.
    # The active log's own mtime is corrected by the client's next write.
    with contextlib.suppress(OSError):
        os.utime(staged.source, ns=(staged.atime_ns, staged.mtime_ns))
    logger.info("archived %s -> %s", staged.source.name, staged.dest.name)
    return staged.dest


def archive_oversized_logs(
    log_dir: Path,
    threshold_mb: int,
    on_rotated: Callable[[Path], None] | None = None,
) -> list[Path]:
    """Stage and swap in one call; returns the archive paths.

    The service splits these across two threads (see the module docstring);
    this is the whole sweep for callers that are already on the right one.
    """
    archived = [
        finish_archive(item, on_rotated) for item in stage_oversized_logs(log_dir, threshold_mb)
    ]
    return [path for path in archived if path is not None]


class LogArchiveService:
    """Driver-tick hook: runs the sweep at most once per CHECK_INTERVAL_S."""

    def __init__(
        self,
        get_log_dir,
        is_enabled,
        get_threshold_mb,
        on_rotated: Callable[[Path], None] | None = None,
        spawn: Spawn | None = None,
    ) -> None:
        self._get_log_dir = get_log_dir
        self._is_enabled = is_enabled
        self._get_threshold_mb = get_threshold_mb
        self._on_rotated = on_rotated
        self._last_check = 0.0
        self._job = BackgroundJob("log-archive", spawn=spawn)
        #: Copies the background sweep has finished, waiting for their swap.
        self._staged: list[StagedArchive] = []

    def tick(self, _now: datetime) -> None:
        if self._staged:
            # We are on the driver thread: empty the logs here, next to the
            # tail, rather than on the thread that made the copies.
            staged, self._staged = self._staged, []
            for item in staged:
                finish_archive(item, self._on_rotated)
            return
        if not self._is_enabled():
            return
        now = time.monotonic()
        if self._last_check and now - self._last_check < CHECK_INTERVAL_S:
            return
        self._last_check = now
        log_dir = Path(self._get_log_dir())
        threshold_mb = self._get_threshold_mb()

        def sweep() -> None:
            self._staged = stage_oversized_logs(log_dir, threshold_mb)

        # Off-thread: this reads the whole log (100 MB by default) and
        # touches nothing the driver owns.
        self._job.submit(sweep)
