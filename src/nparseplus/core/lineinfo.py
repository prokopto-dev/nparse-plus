"""Log-line pre-processing: timestamp stripping and the LineInfo carrier.

EQ log lines look like::

    [Wed Jul 08 21:59:36 2026] You begin casting Clarity.

The bracketed timestamp is parsed into a real ``datetime`` (EQTool behavior;
upstream nparse discarded it and used wall-clock time).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

# EQ timestamp format inside the leading brackets.
_TS_FORMAT = "%a %b %d %H:%M:%S %Y"
_TS_LEN = len("[Wed Jul 08 21:59:36 2026] ")


@dataclass(frozen=True, slots=True)
class LineInfo:
    """One pre-processed log line, as fed to the parser chain."""

    raw: str
    message: str
    timestamp: datetime
    line_number: int


def parse_line(raw: str, line_number: int, now: datetime | None = None) -> LineInfo | None:
    """Split an EQ log line into (timestamp, message).

    Returns None for lines too short to carry the timestamp prefix plus
    content. Falls back to ``now`` (or wall clock) when the timestamp is
    malformed, mirroring EQTool's tolerance of corrupt lines.
    """
    raw = raw.rstrip("\r\n")
    if len(raw) <= _TS_LEN or raw[0] != "[":
        return None
    end = raw.find("]")
    if end == -1:
        return None
    message = raw[end + 1 :].strip()
    if not message:
        return None
    try:
        timestamp = datetime.strptime(raw[1:end], _TS_FORMAT)
    except ValueError:
        timestamp = now or datetime.now()
    return LineInfo(raw=raw, message=message, timestamp=timestamp, line_number=line_number)
