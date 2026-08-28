"""Item-cast (clicky) durations — #188.

A clicky's effect is cast at the ITEM's level, not the level of whoever
clicked it, so a level-60 character clicking a low-level item was getting a
wildly inflated timer. The inference and its deliberate blast-radius limits
are what these tests pin.
"""

from __future__ import annotations

import pytest

from nparseplus.core.enums import PlayerClass
from nparseplus.core.spells.durations import get_duration_seconds, match_closest_level
from nparseplus.core.spells.spells_us import SpellBook


@pytest.fixture
def no_generated_table(monkeypatch):
    """Isolate the layer-1 inference from the scraped layer-3 table.

    Tests that are about the INFERENCE must not depend on what the wiki
    happened to say about a particular spell; the generated table gets its
    own tests below.
    """
    from nparseplus.core.spells import itemcasts

    monkeypatch.setattr(itemcasts, "_generated", lambda path=None: {})
    return itemcasts


# -- THE regression guard: self-casts must be untouched ---------------------------


def test_own_cast_never_changes_a_class_that_can_cast_the_spell(spell_book: SpellBook) -> None:
    """Exhaustive over the whole spell database, at every level that class has.

    #188 is about clickies; it must not touch self-casts. For any class that
    appears in a spell's class table, the item-cast flag must make no
    difference whatsoever — the inference is unreachable for them by
    construction, and this proves it over ~8k spells rather than asserting it.
    """
    checked = 0
    for spell in spell_book.spells:
        for player_class, class_level in spell.class_levels.items():
            for level in (1, class_level, class_level + 5, 60):
                before = get_duration_seconds(spell, player_class, level, own_cast=False)
                after = get_duration_seconds(spell, player_class, level, own_cast=True)
                assert after == before, (
                    f"{spell.name!r} changed for {player_class.name} at level {level}: "
                    f"{before}s -> {after}s"
                )
                checked += 1
    assert checked > 5_000, f"guard covered only {checked} combinations"


def test_observer_paths_are_untouched(spell_book: SpellBook) -> None:
    """A spell somebody ELSE cast keeps EQTool's answer (TestSlowForNecro)."""
    spell = spell_book.spell_by_name("Turgur's Insects")
    assert spell is not None
    # A necro cannot cast it; watching a shaman land it still reads ~6 min.
    seconds = get_duration_seconds(spell, PlayerClass.NECROMANCER, 60)
    assert abs(seconds / 60.0 - 6) < 0.2


# -- the fix ----------------------------------------------------------------------


def test_clicky_uses_the_item_level_not_the_players(
    spell_book: SpellBook, no_generated_table
) -> None:
    spell = spell_book.spell_by_name("Levitate")
    assert spell is not None
    # Warriors cannot cast Levitate, so a level-60 warrior casting it clicked
    # an item. The minimum class level (druid/shaman 14) is the item's level.
    clicky = get_duration_seconds(spell, PlayerClass.WARRIOR, 60, own_cast=True)
    assert clicky == 312

    # What it used to do — and still does for an observer — is the druid-60 answer.
    self_cast = get_duration_seconds(spell, PlayerClass.DRUID, 60, own_cast=True)
    assert self_cast == 1140
    assert get_duration_seconds(spell, PlayerClass.WARRIOR, 60, own_cast=False) == 1140

    assert clicky < self_cast


def test_clicky_duration_does_not_scale_with_the_clickers_level(
    spell_book: SpellBook, no_generated_table
) -> None:
    """The heart of the bug: the same item must read the same at any level."""
    spell = spell_book.spell_by_name("Levitate")
    assert spell is not None
    durations = {
        get_duration_seconds(spell, PlayerClass.WARRIOR, level, own_cast=True)
        for level in (1, 20, 40, 55, 60)
    }
    assert len(durations) == 1, f"item duration varied with the clicker's level: {durations}"


def test_unknown_class_keeps_the_old_behaviour(spell_book: SpellBook) -> None:
    """An unknown class is indistinguishable from an item cast — don't guess."""
    spell = spell_book.spell_by_name("Levitate")
    assert spell is not None
    assert get_duration_seconds(spell, None, 60, own_cast=True) == get_duration_seconds(
        spell, None, 60, own_cast=False
    )


def test_classless_spell_does_not_crash_the_inference(spell_book: SpellBook) -> None:
    """18 disciplines in the data carry no class table at all — min() of an
    empty table would raise, so the branch requires a non-empty one."""
    classless = [s for s in spell_book.spells if not s.class_levels]
    assert classless, "fixture no longer has a classless spell to guard"
    for spell in classless[:50]:
        get_duration_seconds(spell, PlayerClass.WARRIOR, 60, own_cast=True)


# -- epics and the curated layer ---------------------------------------------------


@pytest.mark.parametrize(
    ("spell_name", "epic_class"),
    [
        ("Wrath of Nature", PlayerClass.DRUID),
        ("Celestial Tranquility", PlayerClass.MONK),
        ("Manifest Elements", PlayerClass.MAGICIAN),
    ],
)
def test_epic_clickies_still_work_through_the_existing_fixup(
    spell_book: SpellBook, spell_name: str, epic_class: PlayerClass
) -> None:
    """_apply_epic_fixup writes classes[epic_class] = 46, so the epic's owner
    takes the ordinary castable branch and never reaches the inference."""
    spell = spell_book.spell_by_name(spell_name)
    assert spell is not None
    assert spell.class_levels == {epic_class: 46}
    assert match_closest_level(spell, epic_class, 60, own_cast=True) == 60
    assert get_duration_seconds(spell, epic_class, 60, own_cast=True) == get_duration_seconds(
        spell, epic_class, 60, own_cast=False
    )


def test_castable_by_everyone_fixup_keeps_its_level(spell_book: SpellBook) -> None:
    """The OTHER:46 fixup rows are item clickies; the inference must land on
    46 rather than the clicker's level."""
    spell = spell_book.spell_by_name("Aura of Blue Petals")
    assert spell is not None
    assert spell.class_levels == {PlayerClass.OTHER: 46}
    assert match_closest_level(spell, PlayerClass.WARRIOR, 60, own_cast=True) == 46


def test_curated_table_wins_over_the_inference(
    spell_book: SpellBook, monkeypatch, no_generated_table
) -> None:
    """Layer 2: a hand-curated level overrides the minimum-class-level guess."""
    itemcasts = no_generated_table
    spell = spell_book.spell_by_name("Levitate")
    assert spell is not None
    assert match_closest_level(spell, PlayerClass.WARRIOR, 60, own_cast=True) == 14

    monkeypatch.setitem(itemcasts._CURATED, "Levitate", 33)
    assert match_closest_level(spell, PlayerClass.WARRIOR, 60, own_cast=True) == 33


def test_curated_table_is_ignored_for_a_class_that_can_cast(
    spell_book: SpellBook, monkeypatch
) -> None:
    """Even a curated entry must not reach a self-cast."""
    from nparseplus.core.spells import itemcasts

    spell = spell_book.spell_by_name("Levitate")
    assert spell is not None
    monkeypatch.setitem(itemcasts._CURATED, "Levitate", 30)
    assert match_closest_level(spell, PlayerClass.DRUID, 60, own_cast=True) == 60


def test_generated_table_absent_degrades_to_the_inference(tmp_path) -> None:
    """Layers 1 and 2 ship before the scrape; a missing file is not an error."""
    from nparseplus.core.spells.itemcasts import _generated

    assert _generated(tmp_path / "does-not-exist.json") == {}


# -- end to end through the real parser chain --------------------------------------


def test_clicky_end_to_end_through_the_parsers(spell_book: SpellBook) -> None:
    """A level-60 character casts Levitate. A warrior can only have clicked an
    item; a druid cast it from their book. Same lines, different timers."""
    from datetime import timedelta

    from tests.core.spells.conftest import T0

    from nparseplus.core.bus import EventBus
    from nparseplus.core.handlers.spell_timers import SpellTimerHandler
    from nparseplus.core.lineinfo import LineInfo
    from nparseplus.core.parsers.base import ParseContext
    from nparseplus.core.parsers.you_begin_casting import YouBeginCastingParser
    from nparseplus.core.parsers.you_finish_casting import YouFinishCastingParser
    from nparseplus.core.player import ActivePlayer
    from nparseplus.core.spells.spells_us import SPACE_YOU
    from nparseplus.core.timers import TimersService

    def cast_levitate_as(player_class: PlayerClass) -> float:
        spell_book.casting.clear()
        bus = EventBus()
        player = ActivePlayer()
        player.name = "Tank"
        player.player_class = player_class
        player.level = 60
        timers = TimersService()
        SpellTimerHandler(bus, player, spell_book, timers)
        ctx = ParseContext(bus=bus, player=player, spells=spell_book)
        parsers = [YouBeginCastingParser(), YouFinishCastingParser()]
        lines = ["You begin casting Levitate.", "Your feet leave the ground."]
        for i, message in enumerate(lines, 1):
            # The completion line must clear the cast-time gate, or the parser
            # falls through to its cast-on-you branch (which is NOT an own cast).
            stamp = T0 + timedelta(seconds=0 if i == 1 else 5)
            line = LineInfo(raw=message, message=message, timestamp=stamp, line_number=i)
            for parser in parsers:
                if parser.handle(line, ctx):
                    break
        row = timers.find("Levitate", SPACE_YOU)
        assert row is not None
        return row.total_duration_s

    warrior = cast_levitate_as(PlayerClass.WARRIOR)
    druid = cast_levitate_as(PlayerClass.DRUID)

    # The druid figure is the regression guard and is pinned exactly: a druid
    # really did cast it at 60, so nothing about #188 may move it.
    assert druid == 1140.0
    # The warrior clicked an item. The exact number comes from the item-cast
    # tables (and moves if the wiki data is regenerated), so assert the
    # property that matters plus agreement with the duration layer itself.
    assert warrior < druid
    assert warrior == float(
        get_duration_seconds(
            spell_book.spell_by_name("Levitate"), PlayerClass.WARRIOR, 60, own_cast=True
        )
    )


# -- layer 3: the generated table ---------------------------------------------------


def test_generated_table_ships_and_is_consulted(spell_book: SpellBook) -> None:
    """The committed scrape must actually reach the duration layer."""
    from nparseplus.core.spells.itemcasts import _generated

    table = _generated()
    assert table, "data/items/item_clickies.json is missing or empty"
    assert all(0 < level <= 65 for level in table.values())

    # Pick a spell the scrape covers that some class can still not cast, and
    # check the table's level is the one that comes back.
    for name, level in sorted(table.items()):
        spell = spell_book.spell_by_name(name)
        if spell is None or PlayerClass.WARRIOR in spell.class_levels:
            continue
        assert match_closest_level(spell, PlayerClass.WARRIOR, 60, own_cast=True) == level
        break
    else:  # pragma: no cover - only if the table stops covering anything
        pytest.fail("no scraped spell was usable for this check")


def test_generated_table_never_reaches_a_self_cast(spell_book: SpellBook) -> None:
    """The scrape must not change a single duration for a class that can cast."""
    from nparseplus.core.spells.itemcasts import _generated

    for name in _generated():
        spell = spell_book.spell_by_name(name)
        if spell is None:
            continue
        for player_class, class_level in spell.class_levels.items():
            for level in (class_level, 60):
                assert get_duration_seconds(
                    spell, player_class, level, own_cast=True
                ) == get_duration_seconds(spell, player_class, level, own_cast=False)


def test_item_clickies_json_passes_its_own_check() -> None:
    """The converter's --check guard, run in-process (no network)."""
    import json
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[3]
    sys.path.insert(0, str(root / "tools"))
    import convert_item_clickies as converter

    document = json.loads(converter.OUTPUT_PATH.read_text(encoding="utf-8"))
    assert converter.validate(document) == []
    assert document["meta"]["generated_by"] == "tools/convert_item_clickies.py"
