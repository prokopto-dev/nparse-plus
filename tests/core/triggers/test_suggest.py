"""suggest_trigger_text — a log line becomes a trigger pattern (#82).

Every case ends the same way: the produced pattern must match the line it was
built from, through the real Trigger model. A suggestion that does not is a
trigger the user has to debug before it ever fires.
"""

from __future__ import annotations

import re

import pytest

from nparseplus.core.triggers.model import Trigger
from nparseplus.core.triggers.suggest import (
    strip_timestamp,
    suggest_trigger_name,
    suggest_trigger_text,
)


def matched(pattern: str, line: str, player_name: str = "", use_regex: bool = True) -> bool:
    trigger = Trigger(search_text=pattern, use_regex=use_regex)
    trigger.player_name = player_name
    return trigger.matches(line)


def captured(pattern: str, line: str, group: str = "name", player_name: str = "") -> str | None:
    """What ``{group}`` actually captured, or None if the pattern missed.

    "Did it match" is the weaker question: a token whose expansion cannot
    consume the text it replaced can still re-match further along the line
    and capture garbage, which reads as success everywhere the user looks.
    """
    trigger = Trigger(search_text=pattern, use_regex=True)
    trigger.player_name = player_name
    if not trigger.matches(line):
        return None
    return trigger._value_hash.get(group)


# -- timestamps ----------------------------------------------------------------


@pytest.mark.parametrize(
    "line",
    [
        "[Wed Jul 15 12:00:00 2026] Gorenaire begins to cast a spell.",
        "[12:00:00] Gorenaire begins to cast a spell.",
        "Gorenaire begins to cast a spell.",
    ],
)
def test_timestamp_prefixes_are_stripped(line: str) -> None:
    # The pipeline matches on LineInfo.message: a pattern carrying the stamp
    # would never fire, and the console renders its own [HH:MM:SS] on top.
    assert strip_timestamp(line) == "Gorenaire begins to cast a spell."
    assert suggest_trigger_text(line).message == "Gorenaire begins to cast a spell."


def test_a_bracket_that_is_not_a_clock_survives() -> None:
    assert strip_timestamp("[Guild] Soandso says hi") == "[Guild] Soandso says hi"


# -- tokens --------------------------------------------------------------------


def test_leading_actor_becomes_a_name_token() -> None:
    line = "Gorenaire begins to cast a spell."
    suggestion = suggest_trigger_text(line)

    assert suggestion.tokens == ("name",)
    # re.escape escapes spaces too; the token is the only unescaped part.
    assert suggestion.pattern == r"{name}\ begins\ to\ cast\ a\ spell\."
    assert matched(suggestion.pattern, line)
    # ...and it is reusable, which is the entire point of tokenising.
    assert matched(suggestion.pattern, "Lord Nagafen begins to cast a spell.")


def test_multi_word_actor_is_captured_whole() -> None:
    line = "Lord Nagafen hits YOU for 500 points of damage."
    suggestion = suggest_trigger_text(line)

    assert suggestion.pattern.startswith(r"{name}\ hits")
    assert captured(suggestion.pattern, line) == "Lord Nagafen"


@pytest.mark.parametrize(
    ("line", "actor"),
    [
        ("Gorenaire begins to cast a spell.", "Gorenaire"),
        ("Lord Nagafen hits YOU for 500 points of damage.", "Lord Nagafen"),
        ("Ixiblat`Fer begins to cast a spell.", "Ixiblat`Fer"),
        ("Gorenaire says, 'Who dares (again)?'", "Gorenaire"),
        ("Gorenaire's corpse falls to the ground.", "Gorenaire"),
    ],
)
def test_every_name_token_captures_exactly_the_text_it_replaced(line: str, actor: str) -> None:
    # The round trip that matters: matching is not enough, the token has to
    # capture what it stood in for or every output expanding {name} is wrong.
    suggestion = suggest_trigger_text(line)

    assert captured(suggestion.pattern, line) == actor
    trigger = Trigger(search_text=suggestion.pattern, use_regex=True)
    trigger.matches(line)
    assert trigger.expand(suggestion.display_text) == line


def test_player_name_becomes_the_context_token() -> None:
    line = "Gorenaire hits Soandso for 500 points of damage."
    suggestion = suggest_trigger_text(line, "Soandso")

    assert suggestion.tokens == ("name", "c")
    assert suggestion.pattern == r"{name}\ hits\ {c}\ for\ 500\ points\ of\ damage\."
    assert matched(suggestion.pattern, line, player_name="Soandso")


def test_player_name_wins_when_it_leads_the_line() -> None:
    # {c} already covers the name; a {name} token over the same span would
    # produce two overlapping groups.
    line = "Soandso tells the guild, 'inc'"
    suggestion = suggest_trigger_text(line, "Soandso")

    assert suggestion.tokens == ("c",)
    assert suggestion.pattern.startswith(r"{c}\ tells")
    assert matched(suggestion.pattern, line, player_name="Soandso")


def test_possessive_player_name_still_tokenises() -> None:
    line = "Gorenaire's corpse falls to the ground."
    suggestion = suggest_trigger_text(line, "Gorenaire")

    assert "{c}" in suggestion.pattern
    assert matched(suggestion.pattern, line, player_name="Gorenaire")


@pytest.mark.parametrize(
    ("line", "actor"),
    [
        ("Gorenaire's corpse falls to the ground.", "Gorenaire"),
        ("Lord Nagafen's pet hits YOU for 12 points of damage.", "Lord Nagafen"),
        ("Ixiblat`Fer's corpse falls to the ground.", "Ixiblat`Fer"),
    ],
)
def test_a_possessive_actor_captures_the_owner_not_the_s(line: str, actor: str) -> None:
    # The apostrophe stays literal and OUT of the token: {name} expands to
    # [\w` ]+, which cannot cross it. Folding the possessive into the name
    # let the pattern re-match from the "s" — it reported a match and
    # captured "s", so the display text expanded to "s corpse falls...".
    suggestion = suggest_trigger_text(line, "Soandso")

    assert suggestion.tokens == ("name",)
    assert suggestion.pattern.startswith(r"{name}'s")
    assert captured(suggestion.pattern, line) == actor


def test_the_actor_class_is_a_subset_of_what_name_can_consume() -> None:
    # The invariant behind the bug above: anything the actor pattern accepts
    # must be consumable by the model's own {name} expansion, or the token
    # cannot match the text it replaced.
    from nparseplus.core.triggers.model import EQ_NAME_CHARS
    from nparseplus.core.triggers.suggest import _ACTOR_WORD

    name_chars = re.compile(EQ_NAME_CHARS)
    actor_word = re.compile(f"^{_ACTOR_WORD}$")
    for code in range(32, 0x2500):
        char = chr(code)
        if actor_word.match(char):
            assert name_chars.match(char), f"{char!r} is an actor char {{name}} cannot consume"


def test_you_lines_get_no_actor_token() -> None:
    line = "You begin casting Clarity."
    suggestion = suggest_trigger_text(line)

    assert suggestion.tokens == ()
    assert suggestion.pattern == r"You\ begin\ casting\ Clarity\."
    assert matched(suggestion.pattern, line)


def test_article_led_lowercase_mob_names_are_left_alone() -> None:
    # The capitalised run is just "A" — tokenising it would yield
    # "{name} cliff golem hits you", which is wrong in a way the user has to
    # notice. No token beats a wrong token.
    line = "A cliff golem hits YOU for 42 points of damage."
    suggestion = suggest_trigger_text(line)

    assert suggestion.tokens == ()
    assert matched(suggestion.pattern, line)


def test_a_line_with_neither_token() -> None:
    line = "It begins to rain."
    suggestion = suggest_trigger_text(line, "Soandso")

    assert suggestion.tokens == ()
    assert suggestion.has_tokens is False
    assert matched(suggestion.pattern, line)


# -- escaping ------------------------------------------------------------------


@pytest.mark.parametrize(
    "line",
    [
        "Gorenaire says, 'Who dares (again)?'",
        "You have gained a level! Welcome to level 60!",
        "Soandso hits a orc pawn for 12 points of damage. [+2]",
        "Your Root spell has worn off.",
        "A shimmering ^ portal opens $ here | now",
        "Soandso tells you, 'cast {c} on me'",
        r"Soandso says, 'C:\path\to\nothing'",
        "The gate is open... 3 + 4 = 7?",
    ],
)
def test_metacharacters_still_match_their_own_line(line: str) -> None:
    suggestion = suggest_trigger_text(line, "Soandso")
    assert matched(suggestion.pattern, line, player_name="Soandso")
    # The literal (plain-text) form is a substring test, not a regex.
    assert matched(suggestion.literal, line, use_regex=False)


def expanded_display(line: str, player_name: str, counter: int = 0) -> str:
    """What the overlay would actually SAY for a trigger made from ``line``.

    The search side and the output side are different languages, and testing
    only the first is how a pattern that matches correctly ends up announcing
    the wrong words.
    """
    suggestion = suggest_trigger_text(line, player_name)
    trigger = Trigger(search_text=suggestion.pattern, use_regex=True)
    trigger.player_name = player_name
    assert trigger.matches(line)
    trigger.current_counter = counter
    return trigger.expand(suggestion.display_text)


def test_a_context_token_in_the_log_is_not_expanded_on_the_overlay() -> None:
    # The log said "cast {c} on me" — literally, in someone's tell. Copying
    # the braces into output text made the overlay announce the player's own
    # name instead, since Trigger.expand rewrites {c} wherever it appears.
    line = "Gorenaire tells you, 'cast {c} on me'"

    said = expanded_display(line, "Zzz")

    assert "Zzz" not in said
    assert said == "Gorenaire tells you, 'cast c on me'"


def test_a_counter_token_in_the_log_does_not_change_per_fire() -> None:
    line = "Gorenaire yells {COUNTER} times"

    first = expanded_display(line, "Zzz", counter=1)
    later = expanded_display(line, "Zzz", counter=99)

    assert first == later == "Gorenaire yells COUNTER times"


def test_the_exact_text_offer_neutralizes_output_tokens_too() -> None:
    line = "Gorenaire tells you, 'cast {c} on me'"
    suggestion = suggest_trigger_text(line, "Zzz")

    # The plain-text SEARCH keeps the braces — it is a substring test, and
    # the line really does contain them.
    assert suggestion.literal == line
    # The display text is expanded, so it must not.
    trigger = Trigger(search_text=suggestion.literal, use_regex=False)
    trigger.player_name = "Zzz"
    assert trigger.matches(line)
    said = trigger.expand(suggestion.literal_display)
    assert "Zzz" not in said
    assert said == "Gorenaire tells you, 'cast c on me'"


def test_neutralizing_leaves_ordinary_text_alone() -> None:
    from nparseplus.core.triggers.suggest import neutralize_output_tokens

    assert neutralize_output_tokens("Gorenaire begins to cast a spell.") == (
        "Gorenaire begins to cast a spell."
    )
    # A brace pair that is not a {word} token is not a token to expand either.
    assert neutralize_output_tokens("a {} b { c } d") == "a {} b { c } d"


def test_a_brace_in_the_log_stays_literal() -> None:
    # Escaped as \{c\}: Trigger._compile's placeholder pass needs "{word}"
    # with no backslash before the closing brace, so this cannot become a
    # token by accident.
    line = "Soandso tells you, 'cast {c} on me'"
    suggestion = suggest_trigger_text(line, "Zzz")

    assert r"\{c\}" in suggestion.pattern
    trigger = Trigger(search_text=suggestion.pattern, use_regex=True)
    trigger.player_name = "Zzz"
    assert trigger.matches(line)
    # A different name in the same slot must NOT match — proof the brace was
    # not expanded into the {c} macro.
    assert not trigger.matches("Soandso tells you, 'cast Zzz on me'")


# -- name / display ------------------------------------------------------------


def test_suggested_name_is_short_and_readable() -> None:
    assert suggest_trigger_name("Gorenaire begins to cast a spell.") == (
        "Gorenaire begins to cast a spell"
    )


def test_long_lines_truncate_on_a_word_boundary() -> None:
    line = "Gorenaire hits Soandso for 500 points of damage and then keeps going forever"
    name = suggest_trigger_name(line)

    assert len(name) <= 48
    assert not name.endswith(" ")
    assert line.startswith(name)


def test_display_text_carries_unescaped_tokens() -> None:
    suggestion = suggest_trigger_text("Gorenaire begins to cast a spell.", "Soandso")

    # Display text is expanded, never compiled: escaping it would leak
    # backslashes onto the overlay.
    assert suggestion.display_text == "{name} begins to cast a spell."
    trigger = Trigger(search_text=suggestion.pattern, use_regex=True)
    assert trigger.matches("Gorenaire begins to cast a spell.")
    assert trigger.expand(suggestion.display_text) == "Gorenaire begins to cast a spell."


def test_blank_lines_suggest_nothing() -> None:
    suggestion = suggest_trigger_text("[Wed Jul 15 12:00:00 2026]   ")
    assert suggestion.message == ""
    assert suggestion.has_tokens is False
