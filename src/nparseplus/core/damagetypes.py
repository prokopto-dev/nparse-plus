"""The damage-type vocabulary: which ``DamageEvent.damage_type`` is a swing.

Its own module because two sides need it and neither should drag in the
other: ``core.parsers.damage`` builds its regexes from these verbs, and
``core.dps`` filters rows by them. Importing the parser just to reach a
frozenset costs ~100 ms — it loads the master NPC list at import — and
``core.dps`` is on the UI's import path.
"""

from __future__ import annotations

# The melee attack verbs, (as-you, as-someone-else). One list, four regexes:
# these used to be spelled out separately in each pattern in parsers/damage.py,
# which is also why the damage_type a DamageEvent carries is "slash" for your
# own swing and "slashes" for another player's — both forms are real, and
# MELEE_DAMAGE_TYPES holds both. Order is preserved from the original
# alternations (the regexes are asserted character-identical in the tests).
MELEE_VERBS: tuple[tuple[str, str], ...] = (
    ("hit", "hits"),
    ("slash", "slashes"),
    ("pierce", "pierces"),
    ("crush", "crushes"),
    ("claw", "claws"),
    ("bite", "bites"),
    ("sting", "stings"),
    ("maul", "mauls"),
    ("gore", "gores"),
    ("punch", "punches"),
    ("kick", "kicks"),
    ("backstab", "backstabs"),
    ("bash", "bashes"),
    ("slice", "slices"),
    ("strike", "strikes"),
)

#: Regex alternations for the two conjugations ("You slash" / "Soandso slashes").
MELEE_SINGULAR = "|".join(singular for singular, _ in MELEE_VERBS)
MELEE_PLURAL = "|".join(plural for _, plural in MELEE_VERBS)

#: Every ``DamageEvent.damage_type`` that came from a weapon or a fist.
MELEE_DAMAGE_TYPES: frozenset[str] = frozenset(verb for pair in MELEE_VERBS for verb in pair)

#: Spell, proc and DoT damage: "<target> was hit by non-melee for N points".
NON_MELEE_DAMAGE_TYPE = "non-melee"


def is_melee(damage_type: str) -> bool:
    """Whether a ``DamageEvent`` came from a swing rather than a spell.

    Unknown types read as NOT melee on purpose: a damage line the parser
    learns later (DoT ticks, say) should not silently join a melee-only
    meter just because nobody revisited this predicate.
    """
    return damage_type in MELEE_DAMAGE_TYPES
