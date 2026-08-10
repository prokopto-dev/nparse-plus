"""AbilityCooldownHandler — the 72-minute Lay on Hands / Harm Touch timers.

These lines name the target, never the user, so most of what matters here is
what the handler REFUSES to claim: an NPC paladin healing itself, an NPC
shadow knight harm-touching you or your tank, and any class that cannot use
the ability at all.
"""

from __future__ import annotations

import pytest
from tests.core.handlers.conftest import Harness

from nparseplus.core.enums import PlayerClass
from nparseplus.core.handlers.ability_cooldowns import (
    HARM_TOUCH,
    LAY_ON_HANDS,
    AbilityCooldownHandler,
    looks_like_npc,
)
from nparseplus.core.timers import YOU_GROUP

RECAST_S = 72 * 60


@pytest.fixture
def h(harness: Harness) -> Harness:
    harness.abilities = AbilityCooldownHandler(
        harness.bus, harness.player, harness.ctx.spells, harness.timers
    )
    return harness


def _row(h: Harness, name: str):
    return h.timers.find(name, YOU_GROUP)


# -- Lay on Hands (paladin) ---------------------------------------------------


@pytest.mark.parametrize(
    "line",
    [
        "You feel a healing touch.",  # laid hands on yourself
        "Grimwald feels a healing touch.",  # laid hands on a group member
    ],
)
def test_paladin_lay_on_hands_starts_72_minute_timer(line: str, h: Harness) -> None:
    h.player.player_class = PlayerClass.PALADIN
    h.push(line)
    row = _row(h, LAY_ON_HANDS)
    assert row is not None, f"{line!r} left no Lay on Hands timer"
    assert row.total_duration_s == float(RECAST_S)


@pytest.mark.parametrize(
    "line",
    [
        "a froglok knight feels a healing touch.",
        "an ancient guardian feels a healing touch.",
        "the Ghoul Lord feels a healing touch.",
    ],
)
def test_npc_healing_itself_is_not_your_lay_on_hands(line: str, h: Harness) -> None:
    """NPC paladins lay hands on themselves at low health — a very common line
    in exactly the zones a paladin fights in."""
    h.player.player_class = PlayerClass.PALADIN
    h.push(line)
    assert _row(h, LAY_ON_HANDS) is None


@pytest.mark.parametrize("player_class", [PlayerClass.WARRIOR, PlayerClass.SHADOW_KNIGHT])
def test_only_paladins_get_a_lay_on_hands_timer(player_class: PlayerClass, h: Harness) -> None:
    h.player.player_class = player_class
    h.push("You feel a healing touch.")
    assert _row(h, LAY_ON_HANDS) is None


# -- Harm Touch (shadow knight) -----------------------------------------------


@pytest.mark.parametrize(
    "line",
    [
        "a froglok knight writhes in the grip of agony.",
        "an ice giant writhes in the grip of agony.",
    ],
)
def test_shadow_knight_harm_touch_starts_72_minute_timer(line: str, h: Harness) -> None:
    h.player.player_class = PlayerClass.SHADOW_KNIGHT
    h.push(line)
    row = _row(h, HARM_TOUCH)
    assert row is not None, f"{line!r} left no Harm Touch timer"
    assert row.total_duration_s == float(RECAST_S)


def test_being_harm_touched_is_not_your_harm_touch(h: Harness) -> None:
    """NPC shadow knights harm-touch on aggro; the self form means it hit YOU."""
    h.player.player_class = PlayerClass.SHADOW_KNIGHT
    h.push("You writhe in the grip of agony.")
    assert _row(h, HARM_TOUCH) is None


def test_npc_harm_touching_a_player_is_not_yours(h: Harness) -> None:
    h.player.player_class = PlayerClass.SHADOW_KNIGHT
    h.push("Grimwald writhes in the grip of agony.")
    assert _row(h, HARM_TOUCH) is None


@pytest.mark.parametrize("player_class", [PlayerClass.WARRIOR, PlayerClass.PALADIN])
def test_only_shadow_knights_get_a_harm_touch_timer(player_class: PlayerClass, h: Harness) -> None:
    h.player.player_class = player_class
    h.push("a froglok knight writhes in the grip of agony.")
    assert _row(h, HARM_TOUCH) is None


def test_no_class_yet_claims_nothing(h: Harness) -> None:
    """Class detection is a guess that may not have happened yet; until it
    does, neither ability is attributable."""
    h.player.player_class = None
    h.push("You feel a healing touch.")
    h.push("a froglok knight writhes in the grip of agony.")
    assert _row(h, LAY_ON_HANDS) is None
    assert _row(h, HARM_TOUCH) is None


# -- the NPC heuristic --------------------------------------------------------


@pytest.mark.parametrize(
    "name, expected",
    [
        ("a froglok knight", True),
        ("an ice giant", True),
        ("the Ghoul Lord", True),
        (" a froglok tad", True),  # cast-on-other targets carry a leading space
        ("Lord Nagafen", True),  # named mob, via the master NPC list
        ("Grimwald", False),
        ("", False),
    ],
)
def test_looks_like_npc(name: str, expected: bool, spell_book) -> None:
    assert looks_like_npc(name, spell_book) is expected
