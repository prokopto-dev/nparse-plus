"""Log-line pre-processing: timestamp stripping and the LineInfo carrier.

EQ log lines look like::

    [Wed Jul 08 21:59:36 2026] You begin casting Clarity.

The bracketed timestamp is parsed into a real ``datetime`` (EQTool behavior;
upstream nparse discarded it and used wall-clock time).

The parse is hand-rolled rather than ``strptime`` for two reasons that come
to the same change. ``%a``/``%b`` read the process ``LC_TIME``: the client
always writes English abbreviations, so under any other locale every line
fails to parse and silently takes the wall-clock fallback below — the whole
app then timestamps by the clock instead of the log. Nothing calls
``setlocale`` today, but a plugin or a future dependency could, and the
failure is invisible. The format is fixed-width and fixed-order, so slicing
the digits is locale-proof; it is also ~3x cheaper on a function that runs
on every log line on the driver thread (``strptime`` was 2.5 us of the
3.3 us it took to parse a line).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

_TS_LEN = len("[Wed Jul 08 21:59:36 2026] ")
# The bracketed stamp itself: "Wed Jul 08 21:59:36 2026".
_STAMP_LEN = _TS_LEN - 3
_MONTHS = {
    "Jan": 1,
    "Feb": 2,
    "Mar": 3,
    "Apr": 4,
    "May": 5,
    "Jun": 6,
    "Jul": 7,
    "Aug": 8,
    "Sep": 9,
    "Oct": 10,
    "Nov": 11,
    "Dec": 12,
}


@dataclass(frozen=True, slots=True)
class LineInfo:
    """One pre-processed log line, as fed to the parser chain."""

    raw: str
    message: str
    timestamp: datetime
    line_number: int


def parse_timestamp(stamp: str) -> datetime | None:
    """``Wed Jul 08 21:59:36 2026`` -> naive local datetime, else None.

    The weekday is redundant with the date and is not checked. Naive local,
    like every timestamp in the app.
    """
    if len(stamp) != _STAMP_LEN:
        return None
    if (
        stamp[3] != " "
        or stamp[7] != " "
        or stamp[10] != " "
        or stamp[13] != ":"
        or stamp[16] != ":"
        or stamp[19] != " "
    ):
        return None
    month = _MONTHS.get(stamp[4:7])
    if month is None:
        return None
    try:
        return datetime(
            int(stamp[20:24]),
            month,
            int(stamp[8:10]),
            int(stamp[11:13]),
            int(stamp[14:16]),
            int(stamp[17:19]),
        )
    except ValueError:  # non-numeric field, or a date like Feb 31
        return None


def parse_line(raw: str, line_number: int, now: datetime | None = None) -> LineInfo | None:
    """Split an EQ log line into (timestamp, message).

    Returns None for lines too short to carry the timestamp prefix plus
    content. Falls back to ``now`` (or wall clock) when the timestamp is
    malformed, mirroring EQTool's tolerance of corrupt lines.
    """
    raw = raw.rstrip("\r\n").lstrip("﻿")  # EQ logs may open with a BOM
    if len(raw) <= _TS_LEN or raw[0] != "[":
        return None
    end = raw.find("]")
    if end == -1:
        return None
    message = raw[end + 1 :].strip()
    if not message:
        return None
    timestamp = parse_timestamp(raw[1:end])
    if timestamp is None:
        timestamp = now or datetime.now()
    return LineInfo(raw=raw, message=message, timestamp=timestamp, line_number=line_number)


_WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
# Derived from the parse table rather than written out twice: by month
# number, so the two can never disagree about what "Sep" means.
_MONTH_NAMES = tuple(name for name, _ in sorted(_MONTHS.items(), key=lambda kv: kv[1]))


def format_timestamp(when: datetime) -> str:
    """``datetime`` -> ``Wed Jul 08 21:59:36 2026``: the inverse of the parse above.

    Hand-rolled for the same reason the parse is: ``strftime``'s ``%a``/``%b``
    read the process ``LC_TIME`` and the client always writes English, so a
    line built under another locale would be one this app cannot read back.
    """
    return (
        f"{_WEEKDAYS[when.weekday()]} {_MONTH_NAMES[when.month - 1]} "
        f"{when.day:02d} {when.hour:02d}:{when.minute:02d}:{when.second:02d} {when.year:04d}"
    )


def format_line(message: str, when: datetime) -> str:
    """``message`` wrapped in the log's own ``[stamp] `` prefix.

    The one caller is ``core.testalerts``, which pushes synthetic lines through
    the real pipeline; it lives here so it stays paired with the parse it has
    to satisfy.
    """
    return f"[{format_timestamp(when)}] {message}"
