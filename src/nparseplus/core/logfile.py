"""Pure log-tailing state machine (no Qt, no threads).

The app drives this from a worker thread with a ~100 ms cadence (EQTool's
FileReader model — polling is reliable under wine writes where fs-event
notification coalesces or lags).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

_LOG_NAME = re.compile(r"^eqlog_(?P<char>[A-Za-z]+)_(?P<server>[A-Za-z0-9]+)\.txt$")

# On first attach, back up this far and look for the last session start so we
# don't replay hours of history (upstream nparse behavior).
_ATTACH_BACKTRACK_BYTES = 4096
_SESSION_MARKER = b"] Welcome to EverQuest!"


def parse_log_filename(path: Path | str) -> tuple[str, str] | None:
    """eqlog_<Character>_<server>.txt -> (character, server), else None."""
    m = _LOG_NAME.match(os.path.basename(str(path)))
    if not m:
        return None
    return m.group("char"), m.group("server")


def find_active_log(log_dir: Path) -> Path | None:
    """The most recently modified character log in ``log_dir``."""
    best: tuple[float, Path] | None = None
    try:
        entries = list(log_dir.iterdir())
    except OSError:
        return None
    for p in entries:
        if not parse_log_filename(p):
            continue
        try:
            mtime = p.stat().st_mtime
        except OSError:
            continue
        if best is None or mtime > best[0]:
            best = (mtime, p)
    return best[1] if best else None


@dataclass
class LogTail:
    """Tracks a read position in one log file and yields complete new lines."""

    path: Path
    position: int = 0
    _partial: bytes = field(default=b"", repr=False)

    @classmethod
    def attach(cls, path: Path) -> LogTail:
        """Start tailing at end-of-file, rewound to just after the last
        session start if one appears in the final few KB."""
        size = path.stat().st_size
        start = max(0, size - _ATTACH_BACKTRACK_BYTES)
        with path.open("rb") as f:
            f.seek(start)
            chunk = f.read()
        marker = chunk.rfind(_SESSION_MARKER)
        if marker != -1:
            eol = chunk.find(b"\n", marker)
            position = start + (eol + 1 if eol != -1 else len(chunk))
        else:
            position = size
        return cls(path=path, position=position)

    def poll(self) -> list[str]:
        """Return complete new lines since the last poll (may be empty).

        Handles truncation/rotation: if the file shrank below our position,
        restart from the beginning.
        """
        try:
            size = self.path.stat().st_size
        except OSError:
            return []
        if size < self.position:
            self.position = 0
            self._partial = b""
        if size == self.position:
            return []
        with self.path.open("rb") as f:
            f.seek(self.position)
            data = f.read()
        self.position += len(data)
        data = self._partial + data
        # Keep a trailing partial line (no terminator yet) for the next poll.
        if data.endswith(b"\n"):
            self._partial = b""
        else:
            data, _, self._partial = data.rpartition(b"\n")
            data += b"\n"
            if not data.strip():
                return []
        return [
            line for raw in data.splitlines() if (line := raw.decode("utf-8", errors="replace"))
        ]
