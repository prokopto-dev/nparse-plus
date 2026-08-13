"""RootBreakHandler — port of EQTool's RootHasWornOffHandler (#79).

EQtoolsTests has no dedicated root-break test; SpellWornOffOtherTests.cs is
the spec for the event this handler consumes, so its line shapes (and its
Venom of the Snake negative) are mirrored here and pushed through the real
parser chain.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from tests.core.handlers.conftest import FakeSpeaker, Harness

from nparseplus.core.bus import EventBus
from nparseplus.core.events import OverlayEvent, SpellWornOffOtherEvent
from nparseplus.core.handlers.root_break import ROOT_SPELLS, RootBreakHandler
from nparseplus.core.player import ActivePlayer

T0 = datetime(2026, 7, 8, 21, 59, 36)


class Env:
    def __init__(self, overlay: bool = True, audio: bool = True) -> None:
        self.bus = EventBus()
        self.speaker = FakeSpeaker()
        self.overlays: list[OverlayEvent] = []
        self.bus.subscribe(OverlayEvent, self.overlays.append)
        self.handler = RootBreakHandler(
            self.bus,
            ActivePlayer(name="Tester"),
            speaker=self.speaker,
            overlay_enabled=lambda: overlay,
            audio_enabled=lambda: audio,
        )

    def worn_off(self, spell_name: str) -> None:
        self.bus.publish(
            SpellWornOffOtherEvent(
                timestamp=T0,
                line=f"Your {spell_name} spell has worn off.",
                line_number=1,
                spell_name=spell_name,
            )
        )


def test_the_eleven_root_spells_are_the_c_sharp_list() -> None:
    assert ROOT_SPELLS == (
        "Root",
        "Fetter",
        "Enstill",
        "Immobilize",
        "Paralyzing Earth",
        "Grasping Roots",
        "Ensnaring Roots",
        "Enveloping Roots",
        "Engulfing Roots",
        "Engorging Roots",
        "Entrapping Roots",
    )


@pytest.mark.parametrize("spell", ROOT_SPELLS)
def test_every_root_spell_fires(spell: str) -> None:
    env = Env()
    env.worn_off(spell)

    assert env.speaker.spoken == [f"{spell} has worn off!"]
    assert [(o.text, o.foreground) for o in env.overlays] == [(f"{spell} has worn off!", "Red")]


@pytest.mark.parametrize("spell", ["ROOT", "root", "grasping roots", "eNsTiLl"])
def test_casing_is_ignored_and_the_log_spelling_is_kept(spell: str) -> None:
    env = Env()
    env.worn_off(spell)

    # OrdinalIgnoreCase in the C#, and the alert text formats e.SpellName —
    # the name as the log wrote it, not the list entry.
    assert env.speaker.spoken == [f"{spell} has worn off!"]
    assert env.overlays[0].text == f"{spell} has worn off!"


@pytest.mark.parametrize(
    "spell",
    ["Venom of the Snake", "Boil Blood", "Fear", "Rooted", "Roots", "Snare"],
)
def test_non_root_spells_are_ignored(spell: str) -> None:
    env = Env()
    env.worn_off(spell)

    assert env.speaker.spoken == []
    assert env.overlays == []


def test_the_overlay_toggle_suppresses_only_the_overlay() -> None:
    env = Env(overlay=False, audio=True)
    env.worn_off("Root")

    assert env.speaker.spoken == ["Root has worn off!"]
    assert env.overlays == []


def test_the_audio_toggle_suppresses_only_the_speech() -> None:
    env = Env(overlay=True, audio=False)
    env.worn_off("Root")

    assert env.speaker.spoken == []
    assert len(env.overlays) == 1


def test_both_toggles_off_is_silent() -> None:
    env = Env(overlay=False, audio=False)
    env.worn_off("Root")

    assert env.speaker.spoken == []
    assert env.overlays == []


def test_end_to_end_through_the_parser_chain(harness: Harness) -> None:
    speaker = FakeSpeaker()
    overlays: list[OverlayEvent] = []
    harness.bus.subscribe(OverlayEvent, overlays.append)
    RootBreakHandler(harness.bus, harness.player, speaker=speaker)

    harness.push("Your Root spell has worn off.")
    harness.push("Your Venom of the Snake spell has worn off.")

    assert speaker.spoken == ["Root has worn off!"]
    assert [(o.text, o.foreground) for o in overlays] == [("Root has worn off!", "Red")]
