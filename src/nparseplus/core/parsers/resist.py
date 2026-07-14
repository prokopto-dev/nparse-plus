"""Resist messages — port of ResistParser.cs.

"You resist the <spell> spell!" (their cast, you resisted) and
"Your target resisted the <spell> spell." (your cast, they resisted).
"""

from __future__ import annotations

from nparseplus.core.events import ResistSpellEvent
from nparseplus.core.lineinfo import LineInfo
from nparseplus.core.parsers.base import ParseContext

_YOU_RESIST = "You resist the "
_TARGET_RESISTED = "Your target resisted the "


class ResistParser:
    def handle(self, line: LineInfo, ctx: ParseContext) -> bool:
        if ctx.spells is None:
            return False
        message = line.message

        if message.startswith(_YOU_RESIST):
            spell_name = message.replace(_YOU_RESIST, "").replace(" spell!", "").strip()
            spell = ctx.spells.spell_by_name(spell_name)
            if spell is not None:
                ctx.bus.publish(
                    ResistSpellEvent(
                        timestamp=line.timestamp,
                        line=message,
                        line_number=line.line_number,
                        spell=spell,
                        is_you=True,
                    )
                )
                return True

        if message.startswith(_TARGET_RESISTED):
            spell_name = message.replace(_TARGET_RESISTED, "").replace(" spell.", "").strip()
            spell = ctx.spells.spell_by_name(spell_name)
            if spell is not None:
                ctx.bus.publish(
                    ResistSpellEvent(
                        timestamp=line.timestamp,
                        line=message,
                        line_number=line.line_number,
                        spell=spell,
                        is_you=False,
                    )
                )
                return True

        return False
