"""suggest_trigger_text — a log line becomes a trigger pattern (#82).

Every case ends the same way: the produced pattern must match the line it was
built from, through the real Trigger model. A suggestion that does not is a
trigger the user has to debug before it ever fires.
"""

from __future__ import annotations

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
    assert matched(suggestion.pattern, line)


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
