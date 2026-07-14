"""Buff-fade messages — ports of SpellWornOfSelfParser.cs and
SpellWornOffOtherParser.cs.

Self: the spell_fades text from spells_us.txt ("Your skin returns to
normal.") looked up against the worn-off table; may be ambiguous, so the
event carries every candidate name.

Other: "Your <spell> spell has worn off." emitted for your own spells on
other targets.
"""

from __future__ import annotations

import re

from nparseplus.core.events import SpellWornOffOtherEvent, SpellWornOffSelfEvent
from nparseplus.core.lineinfo import LineInfo
from nparseplus.core.parsers.base import ParseContext

_WORN_OFF_OTHER_RE = re.compile(r"^Your (?P<spell_name>[\w ]+) spell has worn off\.")


class SpellWornOffSelfParser:
    def handle(self, line: LineInfo, ctx: ParseContext) -> bool:
        if ctx.spells is None:
            return False
        names = tuple(spell.name for spell in ctx.spells.worn_off(line.message))
        if not names:
            return False
        ctx.bus.publish(
            SpellWornOffSelfEvent(
                timestamp=line.timestamp,
                line=line.message,
                line_number=line.line_number,
                spell_names=names,
            )
        )
        return True


class SpellWornOffOtherParser:
    def handle(self, line: LineInfo, ctx: ParseContext) -> bool:
        match = _WORN_OFF_OTHER_RE.match(line.message)
        if match is None:
            return False
        ctx.bus.publish(
            SpellWornOffOtherEvent(
                timestamp=line.timestamp,
                line=line.message,
                line_number=line.line_number,
                spell_name=match.group("spell_name"),
            )
        )
        return True
