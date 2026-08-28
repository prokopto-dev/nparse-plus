"""The spell matcher's answers are recorded, so a change to them is measured.

`tests/fixtures/spell_match_baseline.json` records what
`match_closest_level_to_spell` resolves every ambiguous cast message in the
pinned spell file to. #177 is what happens without it: the matcher ignored the
player's class for years because nobody could see the whole surface, and both
the fix and the tiebreak REJECTED alongside it could only be settled by
counting rather than arguing. A diff in that file is not a failure — it is the
change, made reviewable.

Same convention as tools/gen_registry_schema.py: the artifact is committed and
`--check` guards it against drift.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from nparseplus.core.enums import PlayerClass
from nparseplus.core.spells.matching import SpellMatchMode, match_closest_level_to_spell
from nparseplus.core.spells.spells_us import SpellBook

REPO = Path(__file__).resolve().parents[3]
TOOL = REPO / "tools" / "gen_spell_match_baseline.py"
BASELINE = REPO / "tests" / "fixtures" / "spell_match_baseline.json"


@pytest.fixture(scope="module")
def baseline() -> dict:
    return json.loads(BASELINE.read_text(encoding="utf-8"))


def test_the_committed_baseline_is_current() -> None:
    """Regenerate with `uv run python tools/gen_spell_match_baseline.py` and
    review the diff: every line that moves is a cast message whose resolution
    changed."""
    result = subprocess.run(
        [sys.executable, str(TOOL), "--check"],
        capture_output=True,
        text=True,
        cwd=REPO,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_the_baseline_covers_the_whole_ambiguous_surface(
    baseline: dict, spell_book: SpellBook
) -> None:
    """A guard that quietly stopped covering things is worse than none."""
    ambiguous = {
        message
        for index in (
            spell_book._cast_on_you_spells,
            spell_book._cast_other_spells,
            spell_book._you_cast_spells,
        )
        for message, candidates in index.items()
        if len(candidates) > 1
    }
    assert set(baseline["messages"]) == ambiguous
    assert len(ambiguous) > 700, "the fixture should carry hundreds of shared cast messages"


def test_bystander_mode_does_not_read_the_players_class(
    baseline: dict, spell_book: SpellBook
) -> None:
    """The invariant the baseline's shape depends on, checked directly.

    The `bystander` section has no class axis because EQTool's rule reads the
    level only. If that ever stops being true the recorded answers become a
    lie about 14 of the 15 classes, so it is asserted rather than assumed
    (the generator refuses to write the file too).
    """
    for message, record in baseline["messages"].items():
        candidates = (
            spell_book.cast_on_you(message)
            or spell_book.cast_on_other(message)
            or spell_book.you_cast(message)
        )
        for level_text, expected in record["bystander"].items():
            for player_class in PlayerClass:
                found = match_closest_level_to_spell(
                    candidates, player_class, int(level_text), mode=SpellMatchMode.BYSTANDER
                )
                assert found is not None and found.name == expected, (
                    f"{message!r} {player_class.name} {level_text}"
                )


def test_the_class_rule_has_a_bounded_footprint(baseline: dict) -> None:
    """`participant_overrides` is exactly where the player's class changes the
    answer — the #177 fix's whole surface, and empty before it. Pinned loosely
    (it moves with the spell file) but pinned: a change that silently doubled
    it would be a change to what the matcher means."""
    touched = [r for r in baseline["messages"].values() if r["participant_overrides"]]
    overrides = sum(len(r["participant_overrides"]) for r in baseline["messages"].values())
    assert 100 < len(touched) < 400
    assert 500 < overrides < 2000


def test_the_reported_symptoms_are_recorded(baseline: dict) -> None:
    """The three cases from the bug report, read out of the committed file."""
    wolf = baseline["messages"]["you feel the spirit of wolf enter you."]
    assert wolf["participant_overrides"]["SHAMAN:45"] == "Spirit of Wolf"
    assert wolf["participant_overrides"]["RANGER:45"] == "Spirit of Wolf"

    eyes = baseline["messages"]["your eyes tingle."]
    assert eyes["participant_overrides"]["MAGICIAN:30"] == "See Invisible"
    assert eyes["participant_overrides"]["SHAMAN:45"] == "Ultravision"

    # The classed Levitate now heads its own candidate list, so it wins for
    # every class and in both modes — including the observer's.
    levitate = baseline["messages"]["your feet leave the ground."]
    assert levitate["candidates"][0] == "Levitate"
    assert set(levitate["bystander"].values()) == {"Levitate"}


def test_the_observed_spirit_of_wolf_gap_is_recorded_not_hidden(baseline: dict) -> None:
    """#177 asks that an observed Spirit of Wolf resolve correctly regardless of
    the observer. It does NOT, and this pins the fact rather than leaving it a
    box nobody ticked.

    Bystander mode is EQTool's rule unchanged, and Pack Spirit (druid 39) beats
    Spirit of Wolf (ranger 30 / druid 14 / shaman 9) on level distance for an
    observer in the 39-60 band. Nothing strong enough to reverse that survives
    the EQtoolsTests cases in test_matching.py, and the tiebreak proposed for it
    measured as a net loss (see match_closest_level_to_spell's docstring). The
    Timers window's "Other matches" is the answer instead.

    If a future change fixes this, the assertion flips and the baseline diff
    shows what else moved with it — which is the point.
    """
    wolf = baseline["messages"]["you feel the spirit of wolf enter you."]
    assert wolf["bystander"]["45"] == "Pack Spirit"
    assert wolf["bystander"]["60"] == "Pack Spirit"
