"""FTEHandler — FTE announcements and the raid-rule timers (FTETests.cs
covers the parser; these cover the handler)."""

from __future__ import annotations

import pytest
from tests.core.handlers.conftest import FakeSpeaker, Harness

from nparseplus.core.enums import Server
from nparseplus.core.events import OverlayEvent
from nparseplus.core.handlers.fte import (
    LODIZAL_RULE_SECONDS,
    NINETY_SEVEN_RULE_SECONDS,
    NINETY_SIX_RULE_SECONDS,
    FTEHandler,
)
from nparseplus.core.timers import MOB_TIMER_GROUP


@pytest.fixture
def h(harness: Harness) -> Harness:
    harness.speaker = FakeSpeaker()
    harness.handler = FTEHandler(harness.bus, harness.player, harness.timers, harness.speaker)
    return harness


def test_fte_line_speaks_and_overlays(h: Harness) -> None:
    h.push("Cekenar engages Tzvia!")
    assert h.speaker.spoken == ["Tzvia F T E Cekenar"]
    overlay = h.collector.single(OverlayEvent)
    assert overlay.text == "Tzvia FTE Cekenar"
    assert h.timers.snapshot() == []  # Cekenar has no raid-rule timer


def test_multiword_npc_name(h: Harness) -> None:
    h.push("Dagarn the Destroyer engages Tzvia!")
    assert h.collector.single(OverlayEvent).text == "Tzvia FTE Dagarn the Destroyer"


def test_97_percent_rule_timer(h: Harness) -> None:
    h.push("Zlandicar engages Tzvia!")
    row = h.timers.find("--97% Rule-- Zlandicar", MOB_TIMER_GROUP)
    assert row is not None
    assert row.total_duration_s == float(NINETY_SEVEN_RULE_SECONDS)


def test_96_percent_rule_on_green(h: Harness) -> None:
    h.player.server = Server.GREEN
    h.push("Lord Yelinak engages Tzvia!")
    row = h.timers.find("--96% Rule-- Lord Yelinak", MOB_TIMER_GROUP)
    assert row is not None
    assert row.total_duration_s == float(NINETY_SIX_RULE_SECONDS)


def test_97_percent_rule_off_green(h: Harness) -> None:
    h.player.server = Server.BLUE
    h.push("Lord Yelinak engages Tzvia!")
    assert h.timers.find("--97% Rule-- Lord Yelinak", MOB_TIMER_GROUP) is not None


def test_lodizal_five_minute_rule(h: Harness) -> None:
    h.push("Lodizal engages Tzvia!")
    row = h.timers.find("--5 Minute Rule-- Lodizal", MOB_TIMER_GROUP)
    assert row is not None
    assert row.total_duration_s == float(LODIZAL_RULE_SECONDS)
