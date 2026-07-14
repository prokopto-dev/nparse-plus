"""Zoning "LOADING, PLEASE WAIT..." parser (port of EQTool LoadingPleaseWaitParser.cs)."""

from __future__ import annotations

from nparseplus.core.events import LoadingPleaseWaitEvent
from nparseplus.core.lineinfo import LineInfo
from nparseplus.core.parsers.base import ParseContext

_LOADING_LINE = "LOADING, PLEASE WAIT..."


class LoadingPleaseWaitParser:
    def handle(self, line: LineInfo, ctx: ParseContext) -> bool:
        if line.message != _LOADING_LINE:
            return False
        ctx.bus.publish(
            LoadingPleaseWaitEvent(
                timestamp=line.timestamp, line=line.message, line_number=line.line_number
            )
        )
        return True
