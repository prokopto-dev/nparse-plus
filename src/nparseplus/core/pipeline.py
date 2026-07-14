"""The line-processing pipeline: raw line -> LineInfo -> parser chain -> bus.

Port of EQTool's LogParser.MainRun (Services/LogParser.cs). The app drives
``process`` from a worker thread fed by ``core.logfile.LogTail`` at ~100 ms.
"""

from __future__ import annotations

import logging
from datetime import datetime

from nparseplus.core.events import LineEvent
from nparseplus.core.lineinfo import LineInfo, parse_line
from nparseplus.core.parsers.base import LineParser, ParseContext

logger = logging.getLogger(__name__)

# EQTool rewrites these variable-suffix death messages to a stable prefix
# before parsing (LogParser.cs MainRun).
_REWRITES = (
    "Your body begins to rot.  You have taken ",
    "Your eardrums rupture.  You have taken ",
)


class LogPipeline:
    def __init__(self, parsers: list[LineParser], ctx: ParseContext) -> None:
        self._parsers = parsers
        self._ctx = ctx
        self._line_counter = 0
        # Last time a "You..." line was seen — EQTool uses this for the
        # sharing idle-suppression and death-loop logic.
        self.last_you_activity: datetime | None = None
        self.last_entry_time: datetime | None = None

    def process(self, raw: str) -> None:
        self._line_counter += 1
        info = parse_line(raw, self._line_counter)
        if info is None:
            return
        message = info.message
        for prefix in _REWRITES:
            if message.startswith(prefix):
                message = prefix.rstrip()[: prefix.index(".") + 1]
                info = LineInfo(
                    raw=info.raw,
                    message=message,
                    timestamp=info.timestamp,
                    line_number=info.line_number,
                )
                break
        if message.startswith("You"):
            self.last_you_activity = datetime.now()
        self.last_entry_time = info.timestamp

        for parser in self._parsers:
            try:
                if parser.handle(info, self._ctx):
                    break
            except Exception:
                logger.exception("parser %r failed on line: %s", type(parser).__name__, raw)
        # The raw-line firehose fires whether or not a parser consumed it.
        self._ctx.bus.publish(
            LineEvent(timestamp=info.timestamp, line=info.message, line_number=info.line_number)
        )
