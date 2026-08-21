"""format_fight_details — the EQTool clipboard string, verbatim (#78).

The C# has no test for ``copytoclipboard``; the spec is the call site itself
(``EQTool/UI/DPSMeter.xaml.cs`` at d8e8084f), and what these pin is the exact
string it builds, separators included. EQTool users paste these into the same
raid channels, so the format is a wire format.
"""

from collections.abc import Callable
from datetime import datetime, timedelta

from nparseplus.core.dps import FightRow, FightTracker, format_fight_details
from nparseplus.core.events import DamageEvent


def _row(**overrides: object) -> FightRow:
    base = dict(
        target_name="Lady Vox",
        attacker_name="You",
        level=None,
        is_dead=False,
        is_your_damage=True,
        is_your_pet=False,
        total_damage=1000,
        target_total_damage=1000,
        percent_of_total=100,
        trailing_damage=0,
        dps=0,
        total_dps=50,
        highest_hit=0,
        total_seconds=20,
    )
    base.update(overrides)
    return FightRow(**base)  # type: ignore[arg-type]


def test_multi_attacker_line_is_the_exact_eqtool_string() -> None:
    rows = [
        _row(
            attacker_name="You",
            total_damage=6000,
            target_total_damage=10000,
            percent_of_total=60,
            total_dps=120,
        ),
        _row(
            attacker_name="Vebanab",
            is_your_damage=False,
            total_damage=3000,
            target_total_damage=10000,
            percent_of_total=30,
            total_dps=60,
        ),
        # EQ names take backticks, and the parse must not mangle one.
        _row(
            attacker_name="Gnarl`bek",
            is_your_damage=False,
            total_damage=1000,
            target_total_damage=10000,
            percent_of_total=10,
            total_dps=20,
        ),
    ]
    assert format_fight_details(rows) == (
        "Fight Details: Lady Vox Dmg: 10000    "
        "You 60% DPS:120 DMG:6000 / "
        "Vebanab 30% DPS:60 DMG:3000 / "
        "Gnarl`bek 10% DPS:20 DMG:1000"
    )


def test_the_separators_are_four_spaces_and_a_slash() -> None:
    """Both are the C#'s literals, not formatting choices."""
    text = format_fight_details(
        [
            _row(attacker_name="You", total_damage=2, target_total_damage=3, percent_of_total=66),
            _row(
                attacker_name="Vebanab",
                is_your_damage=False,
                total_damage=1,
                target_total_damage=3,
                percent_of_total=33,
            ),
        ]
    )
    head, _, tail = text.partition("Dmg: 3")
    assert head == "Fight Details: Lady Vox "
    assert tail.startswith("    You ")
    assert " / Vebanab " in tail


def test_attackers_are_sorted_by_total_damage_descending() -> None:
    """OrderByDescending(a => a.TotalDamage) — regardless of the input order."""
    text = format_fight_details(
        [
            _row(attacker_name="Small", total_damage=1, target_total_damage=101),
            _row(attacker_name="Big", total_damage=100, target_total_damage=101),
        ]
    )
    assert text.index("Big ") < text.index("Small ")


def test_a_zero_damage_group_still_formats() -> None:
    """``TargetTotalDamage`` of 0 makes every percentage 0; nothing divides."""
    assert format_fight_details(
        [_row(attacker_name="You", total_damage=0, target_total_damage=0, percent_of_total=0)]
    ) == ("Fight Details: Lady Vox Dmg: 0    You 0% DPS:50 DMG:0")


def test_an_empty_group_has_no_target_to_name() -> None:
    assert format_fight_details([]) == ""


def test_dps_is_the_whole_fight_number_not_the_trailing_window(
    tracker: FightTracker, hit: Callable[..., DamageEvent], t0: datetime
) -> None:
    """``DPS:`` is TotalDPS: a parse describes the fight, not its last 12 s."""
    tracker.add_damage(hit("You", "a gnoll", 600))
    tracker.add_damage(hit("You", "a gnoll", 600, offset_s=30))
    rows = tracker.snapshot(t0 + timedelta(seconds=30))
    assert rows[0].dps != rows[0].total_dps  # the two numbers really differ here
    assert f"DPS:{rows[0].total_dps} " in format_fight_details(rows)


def test_a_live_snapshot_round_trips(
    tracker: FightTracker, hit: Callable[..., DamageEvent], t0: datetime
) -> None:
    """snapshot() order is already the copy order, so no re-sort is visible."""
    tracker.add_damage(hit("Vebanab", "a gnoll", 30))
    tracker.add_damage(hit("You", "a gnoll", 70, offset_s=1))
    rows = tracker.snapshot(t0 + timedelta(seconds=1))
    text = format_fight_details(rows)
    assert text.startswith("Fight Details: a gnoll Dmg: 100    You 70% ")
    assert " / Vebanab 30% " in text
