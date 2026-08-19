"""The line-processing pipeline: raw line -> LineInfo -> parser chain -> bus.

Port of EQTool's LogParser.MainRun (Services/LogParser.cs). The app drives
``process`` from a worker thread fed by ``core.logfile.LogTail`` at ~100 ms.

That thread owns the chain. ``LogDriver`` wires ``set_command_sink`` to its
command inbox when it takes the pipeline, so a plugin adding or removing a
parser from the GUI thread lands between lines instead of shifting the index
of a chain that is being walked.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Callable
from datetime import datetime
from typing import Protocol

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


class CommandSink(Protocol):
    """``LogDriver.submit_to_driver`` — kept structural so core stays acyclic."""

    def __call__(self, fn: Callable[[], None], *, label: str) -> None: ...


class LogPipeline:
    def __init__(self, parsers: list[LineParser], ctx: ParseContext) -> None:
        self._parsers = parsers
        self._ctx = ctx
        self._line_counter = 0
        # Last time a "You..." line was seen — EQTool uses this for the
        # sharing idle-suppression and death-loop logic.
        self.last_you_activity: datetime | None = None
        self.last_entry_time: datetime | None = None
        self._submit: CommandSink | None = None

    def set_command_sink(self, submit: CommandSink | None) -> None:
        """Route chain mutations onto the thread that walks the chain.

        ``LogDriver`` wires this to its own inbox in ``__init__``. With no
        sink — a pipeline nobody drives: replay harnesses, tests, the SDK's
        fakes — mutations apply immediately, exactly as they did before this
        seam existed. There is no thread to defer to.
        """
        self._submit = submit

    def append_parser(self, parser: LineParser) -> None:
        """Append a parser after the built-in chain (the plugin seam).

        First-match-wins is preserved: appended parsers only see lines no
        built-in consumed. Safe from any thread: the append is routed through
        the driver's command inbox and lands between lines.
        """
        self._mutate(
            lambda: self._parsers.append(parser),
            label=f"add parser {type(parser).__name__}",
        )

    def remove_parser(self, parser: LineParser) -> None:
        """Remove a previously appended parser (plugin unwind); no-op if absent.

        Routed like ``append_parser``, and this is the direction that made
        the seam necessary: removing from the live list mid-``process``
        shifts every later parser down one and skips one for that line.
        """
        self._mutate(
            lambda: self._remove_now(parser),
            label=f"remove parser {type(parser).__name__}",
        )

    def _remove_now(self, parser: LineParser) -> None:
        with contextlib.suppress(ValueError):
            self._parsers.remove(parser)

    def _mutate(self, fn: Callable[[], None], *, label: str) -> None:
        if self._submit is None:
            fn()
            return
        self._submit(fn, label=label)

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

        # Iterated live, deliberately: the chain is only ever mutated on this
        # thread between lines (set_command_sink), so there is nothing to copy
        # away from and this runs for every line of the log.
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
