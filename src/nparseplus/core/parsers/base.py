"""Parser protocol and the shared context parsers read from.

A parser inspects one pre-processed line and, when it matches, publishes
typed events on the bus and returns True (consuming the line — the chain
stops). Parsers must be cheap for non-matching lines: check a literal
prefix/substring before running a regex.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from nparseplus.core.bus import EventBus
from nparseplus.core.lineinfo import LineInfo

if TYPE_CHECKING:
    from nparseplus.core.player import ActivePlayer
    from nparseplus.core.spells.spells_us import SpellBook
    from nparseplus.core.zones import ZoneDatabase


@dataclass
class ParseContext:
    """Shared state the parser chain reads (and some handlers mutate)."""

    bus: EventBus
    player: ActivePlayer
    spells: SpellBook | None = None
    zones: ZoneDatabase | None = None


@runtime_checkable
class LineParser(Protocol):
    def handle(self, line: LineInfo, ctx: ParseContext) -> bool:
        """Return True if this parser consumed the line."""
        ...
