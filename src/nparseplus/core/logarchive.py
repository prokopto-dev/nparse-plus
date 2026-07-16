"""Log archiving — port of EQTool's LogArchiveService.

When enabled, any ``*.txt`` in the log directory over the size threshold is
moved to ``<log_dir>/archive/<name>_<timestamp>.txt``. EQ re-creates the log
on its next write, and our LogTail treats the shrink as a rotation. Files the
OS refuses to move (in use) are skipped silently, same as the C#.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

CHECK_INTERVAL_S = 60 * 60  # hourly, like EQTool


def archive_oversized_logs(log_dir: Path, threshold_mb: int) -> list[Path]:
    """Move oversized logs into the archive folder; returns the new paths."""
    if threshold_mb <= 0 or not log_dir.is_dir():
        return []
    threshold = threshold_mb * 1024 * 1024
    archive_dir = log_dir / "archive"
    moved: list[Path] = []
    for path in log_dir.glob("*.txt"):
        try:
            if path.stat().st_size < threshold:
                continue
            archive_dir.mkdir(exist_ok=True)
            stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            dest = archive_dir / f"{path.stem}_{stamp}.txt"
            path.rename(dest)
            moved.append(dest)
            logger.info("archived %s -> %s", path.name, dest.name)
        except OSError:
            logger.debug("could not archive %s (in use?)", path, exc_info=True)
    return moved


class LogArchiveService:
    """Driver-tick hook: runs the sweep at most once per CHECK_INTERVAL_S."""

    def __init__(self, get_log_dir, is_enabled, get_threshold_mb) -> None:
        self._get_log_dir = get_log_dir
        self._is_enabled = is_enabled
        self._get_threshold_mb = get_threshold_mb
        self._last_check = 0.0

    def tick(self, _now: datetime) -> None:
        if not self._is_enabled():
            return
        now = time.monotonic()
        if self._last_check and now - self._last_check < CHECK_INTERVAL_S:
            return
        self._last_check = now
        archive_oversized_logs(Path(self._get_log_dir()), self._get_threshold_mb())
