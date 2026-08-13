"""Log archiving — port of EQTool's LogArchiveService.

When enabled, any ``*.txt`` in the log directory over the size threshold is
copied into ``<log_dir>/archive/<name>_<timestamp>.txt`` and then **truncated
in place**, leaving an empty file behind. ``LogTail.poll`` recognizes that as
a rotation and keeps reading — by the byte signature at its read offset, not
by the file having got smaller: this sweep runs on its own thread, so the
client can refill the log to that offset, or past it, before the next 100 ms
poll, and the size would then say nothing was wrong.

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

The sweep runs off the driver thread (``core.background``): copying a 100 MB
log is not something to do on the thread that tails the log.
"""

from __future__ import annotations

import logging
import os
import shutil
import time
from datetime import datetime
from pathlib import Path

from nparseplus.core.background import BackgroundJob, Spawn

logger = logging.getLogger(__name__)

CHECK_INTERVAL_S = 60 * 60  # hourly, like EQTool
# How many times to go back for lines the client appended while we copied.
# Each pass is smaller than the last, so a couple converge; the bound is
# there so a firehose cannot hold the sweep open indefinitely.
CATCHUP_PASSES = 3


def _copy_out_and_truncate(path: Path, dest: Path) -> None:
    """Copy ``path`` to ``dest``, then empty ``path`` without replacing it."""
    stat = path.stat()
    # Opening for write FIRST: on Windows this is what the client's share mode
    # refuses, and failing here means we skip the file having copied nothing.
    # (Copying first would leave a duplicate archive behind every hour on the
    # one platform where the truncate cannot happen.)
    with path.open("r+b") as src:
        # Land the copy on disk under a partial name and rename it into place,
        # so a crash mid-copy cannot leave a short archive looking complete —
        # and never truncate the source before the copy is durable.
        partial = dest.with_name(f".{dest.name}.part")
        with partial.open("wb") as out:
            shutil.copyfileobj(src, out)
            # The client is still writing. Whatever it appends between the
            # last read and the truncate below is gone, so go back for it —
            # that shrinks the hole from "the length of the copy" to the
            # length of one small tail read.
            for _ in range(CATCHUP_PASSES):
                tail = src.read()
                if not tail:
                    break
                out.write(tail)
            out.flush()
            os.fsync(out.fileno())
        partial.replace(dest)
        src.truncate(0)
    # An emptied log keeps its old mtime: `logfile.find_active_log` picks the
    # most recently modified log, and a stale character's truncated file would
    # otherwise look like the newest one and pull the driver off the live log.
    # The active log's own mtime is corrected by the client's next write.
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))


def archive_oversized_logs(log_dir: Path, threshold_mb: int) -> list[Path]:
    """Archive oversized logs and empty them; returns the archive paths."""
    if threshold_mb <= 0 or not log_dir.is_dir():
        return []
    threshold = threshold_mb * 1024 * 1024
    archive_dir = log_dir / "archive"
    archived: list[Path] = []
    for path in log_dir.glob("*.txt"):
        try:
            if path.stat().st_size < threshold:
                continue
            archive_dir.mkdir(exist_ok=True)
            stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            dest = archive_dir / f"{path.stem}_{stamp}.txt"
            _copy_out_and_truncate(path, dest)
            archived.append(dest)
            logger.info("archived %s -> %s", path.name, dest.name)
        except OSError:
            logger.debug("could not archive %s (in use?)", path, exc_info=True)
    return archived


class LogArchiveService:
    """Driver-tick hook: runs the sweep at most once per CHECK_INTERVAL_S."""

    def __init__(
        self,
        get_log_dir,
        is_enabled,
        get_threshold_mb,
        spawn: Spawn | None = None,
    ) -> None:
        self._get_log_dir = get_log_dir
        self._is_enabled = is_enabled
        self._get_threshold_mb = get_threshold_mb
        self._last_check = 0.0
        self._job = BackgroundJob("log-archive", spawn=spawn)

    def tick(self, _now: datetime) -> None:
        if not self._is_enabled():
            return
        now = time.monotonic()
        if self._last_check and now - self._last_check < CHECK_INTERVAL_S:
            return
        self._last_check = now
        log_dir = Path(self._get_log_dir())
        threshold_mb = self._get_threshold_mb()

        def sweep() -> None:
            archive_oversized_logs(log_dir, threshold_mb)

        # Off-thread: the sweep reads and writes the whole log (100 MB by
        # default) and touches nothing the driver owns.
        self._job.submit(sweep)
