"""Own-cast completion and spells landing on you.

Port of YouFinishCasting_SpellCastOnYou_Parser.cs — one parser handles both
YouFinishCastingEvent (your cast landed, matched against the spell you began
casting) and SpellCastOnYouEvent (someone's spell hit you, matched by the
cast_on_you message table).

EQTool also routes "dragon roar" AOEs here via zone NPC data; that depends on
the fight-history service and is not ported yet — those messages fall through
to the plain SpellCastOnYouEvent path.
"""

from __future__ import annotations

import re

from nparseplus.core.events import (
    ResistSpellEvent,
    SpellCastOnYouEvent,
    YouFinishCastingEvent,
    YourSpellInterruptedEvent,
)
from nparseplus.core.lineinfo import LineInfo
from nparseplus.core.parsers.base import ParseContext
from nparseplus.core.spells.matching import match_closest_level_to_spell
from nparseplus.core.spells.spells_us import SPACE_YOU

_PROTECTED_RE = re.compile(
    r"^You try to cast a spell on (?P<target_name>[\w ]+), but they are protected\."
)

# Spells excluded from cast-on-you guesses (ambiguous with better matches).
_IGNORE_SPELLS_FOR_GUESSES = frozenset({"Tigir's Insects"})

# Grace window: a completion message may arrive slightly before cast time ends.
_CAST_TIME_SLOP_MS = 600


class YouFinishCastingParser:
    def handle(self, line: LineInfo, ctx: ParseContext) -> bool:
        if ctx.spells is None:
            return False
        message = line.message
        casting = ctx.spells.casting

        if casting.spell is not None and casting.started_at is not None:
            spell = casting.spell
            elapsed_ms = (line.timestamp - casting.started_at).total_seconds() * 1000.0
            if elapsed_ms >= spell.cast_time_ms - _CAST_TIME_SLOP_MS:
                if message == spell.cast_on_you:
                    ctx.bus.publish(
                        YouFinishCastingEvent(
                            timestamp=line.timestamp,
                            line=message,
                            line_number=line.line_number,
                            spell=spell,
                            target_name=SPACE_YOU,
                        )
                    )
                    return True
                if spell.cast_on_other and message.endswith(spell.cast_on_other):
                    target = message.replace(spell.cast_on_other, "").strip()
                    ctx.bus.publish(
                        YouFinishCastingEvent(
                            timestamp=line.timestamp,
                            line=message,
                            line_number=line.line_number,
                            spell=spell,
                            target_name=target,
                        )
                    )
                    return True
                if spell.name == "Theft of Thought" and message == (
                    "Your target has no mana to affect"
                ):
                    ctx.bus.publish(
                        YourSpellInterruptedEvent(
                            timestamp=line.timestamp,
                            line=message,
                            line_number=line.line_number,
                        )
                    )
                    return True
                if _PROTECTED_RE.match(message):
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

        if message.endswith(".."):
            message = message[:-1]

        candidates = [
            s for s in ctx.spells.cast_on_you(message) if s.name not in _IGNORE_SPELLS_FOR_GUESSES
        ]
        if candidates:
            # Guess Spells off: an ambiguous cast-on-you line is recognized
            # but publishes nothing — still consumed so later parsers don't
            # misread it. (nparseplus option; EQTool's best-guess is always
            # on. ctx without settings keeps guessing on for test harnesses.)
            if (
                len(candidates) > 1
                and ctx.settings is not None
                and not ctx.settings.spellwindow.best_guess_spells
            ):
                return True
            guessed = match_closest_level_to_spell(
                candidates, ctx.player.player_class, ctx.player.level
            )
            if guessed is not None:
                ctx.bus.publish(
                    SpellCastOnYouEvent(
                        timestamp=line.timestamp,
                        line=message,
                        line_number=line.line_number,
                        spell=guessed,
                    )
                )
                return True

        return False
