"""CH chain comms parsing and warn logic — port of
EQtoolsTests/CompleteHealCommsHandlerTests.cs."""

from __future__ import annotations

import pytest
from tests.core.handlers.conftest import T0, FakeSpeaker, Harness

from nparseplus.core.ch_chain import ChainData, parse_ch_cadence, should_warn_of_chain
from nparseplus.core.events import (
    CompleteHealCadenceEvent,
    CompleteHealEvent,
    OverlayEvent,
)
from nparseplus.core.handlers.complete_heal import (
    CH_WARNING_TEXT,
    CompleteHealCommsHandler,
    CompleteHealHandler,
)


@pytest.fixture
def h(harness: Harness, npcs) -> Harness:
    harness.speaker = FakeSpeaker()
    harness.comms_handler = CompleteHealCommsHandler(harness.bus, harness.player, npcs)
    harness.ch_handler = CompleteHealHandler(harness.bus, harness.player, harness.speaker)
    return harness


def ch_events(h: Harness) -> list[CompleteHealEvent]:
    return h.collector.of_type(CompleteHealEvent)


def assert_ch(h: Harness, recipient: str, caster: str, position: str, tag: str = "") -> None:
    event = h.collector.single(CompleteHealEvent)
    assert event.recipient == recipient
    assert event.caster == caster
    assert event.position == position
    assert event.tag == tag


def test_non_ch_shout_ignored(h: Harness) -> None:
    h.push("Curaja shouts, 'ench lfg'")
    assert ch_events(h) == []


def test_gg_tagged_call(h: Harness) -> None:
    h.push("Curaja shouts, 'GG 014 CH -- Wreckognize'")
    assert_ch(h, "Wreckognize", "Curaja", "014")


def test_gg_001_call(h: Harness) -> None:
    h.push("Hanbox shouts, 'GG 001 CH -- Beefwich'")
    assert_ch(h, "Beefwich", "Hanbox", "001")


def test_no_space_before_target(h: Harness) -> None:
    h.push("Hanbox shouts, 'GG 001 CH --Beefwich'")
    assert_ch(h, "Beefwich", "Hanbox", "001")


def test_position_after_target(h: Harness) -> None:
    h.push("Hanbox shouts, 'CH - Beefwich - 001'")
    assert_ch(h, "Beefwich", "Hanbox", "001")


@pytest.mark.parametrize(
    ("content", "position"),
    [
        ("CA RAMP1 CH --Beefwich", "RAMP1"),
        ("CA RAMP2 CH --Beefwich", "RAMP2"),
        ("RAMP2 CH --Beefwich", "RAMP2"),
        ("RAMP01 CH --Beefwich", "RAMP01"),
    ],
)
def test_ramp_positions(h: Harness, content: str, position: str) -> None:
    h.push(f"Hanbox shouts, '{content}'")
    assert_ch(h, "Beefwich", "Hanbox", position)


def test_trailing_text_outside_quote(h: Harness) -> None:
    h.push("Hanbox shouts, 'GG 001 CH --Beefwich' 001")
    assert_ch(h, "Beefwich", "Hanbox", "001")


def test_generic_name_recipient(h: Harness) -> None:
    h.push("Hanbox shouts, 'CH - name - 001'")
    assert_ch(h, "name", "Hanbox", "001")


def test_chat_about_chains_is_ignored(h: Harness) -> None:
    h.push("Vaeric tells the guild, 'Currently signed up as 001 in CH chain'")
    h.push("Vaeric tells the guild, 'Currently signed up as in CH chain'")
    assert ch_events(h) == []


def test_ooc_call(h: Harness) -> None:
    h.push("Wartburg says out of character, 'CA 004 CH -- Sam'")
    assert_ch(h, "Sam", "Wartburg", "004")


def test_ooc_call_no_tag(h: Harness) -> None:
    h.push("Wartburg says out of character, '004 CH - Sam'")
    assert_ch(h, "Sam", "Wartburg", "004")


def test_guild_call(h: Harness) -> None:
    h.push("Hanbox tells the guild, 'GG 001 CH --Beefwich'")
    assert_ch(h, "Beefwich", "Hanbox", "001")


def test_configured_tag_is_captured(h: Harness) -> None:
    h.comms_handler.ch_chain_tag = lambda: "GG"
    h.push("Hanbox tells the guild, 'GG 001 CH --Beefwich'")
    assert_ch(h, "Beefwich", "Hanbox", "001", tag="GG")


def test_configured_tag_mismatch_is_ignored(h: Harness) -> None:
    h.comms_handler.ch_chain_tag = lambda: "GG"
    h.push("Hanbox tells the guild, 'CA 001 CH --Beefwich'")
    assert ch_events(h) == []


def test_configured_tag_is_live(h: Harness) -> None:
    # The tag comes from a provider, so changing its underlying value mid-
    # session re-filters without rebuilding the handler.
    box = {"tag": "CA"}
    h.comms_handler.ch_chain_tag = lambda: box["tag"]
    h.push("Hanbox tells the guild, 'GG 001 CH --Beefwich'")
    assert ch_events(h) == []
    box["tag"] = "GG"
    h.push("Hanbox tells the guild, 'GG 001 CH --Beefwich'")
    assert_ch(h, "Beefwich", "Hanbox", "001", tag="GG")


def test_configured_tag_mismatch_group_message(h: Harness) -> None:
    h.comms_handler.ch_chain_tag = lambda: "GGG"
    h.push("Windarie tells the group, 'Bufzyn 111 --- CH on << Tinialita  >> --- 111'")
    assert ch_events(h) == []


def test_your_own_call_sets_caster_you(h: Harness) -> None:
    h.comms_handler.ch_chain_tag = lambda: "CA"
    h.push("You say out of character, 'CA 002 CH -- Aaryk'")
    assert_ch(h, "Aaryk", "You", "002", tag="CA")


def test_tells_are_ignored(h: Harness) -> None:
    h.push("Kaboomslang -> Distributin: ch plz")
    h.push(
        "You told someone, 'when CH chains are e a 1-2 full rounds of max dmg hits though if u can'"
    )
    assert ch_events(h) == []


def test_short_recipient_is_rejected(h: Harness) -> None:
    h.push("somecleric tells the guild, '003 - CH 5T'")
    assert ch_events(h) == []


def test_decorated_call(h: Harness) -> None:
    h.push("Windarie auctions, '111 --- CH << Mandair  >> --- 111'")
    assert_ch(h, "Mandair", "Windarie", "111")


def test_decorated_call_with_arrows(h: Harness) -> None:
    h.push("Mutao auctions, '777 CH <>> Mandair <<> 777'")
    assert_ch(h, "Mandair", "Mutao", "777")


def test_letter_positions(h: Harness) -> None:
    h.push("Mutao auctions, 'AAA CH <>> Mandair <<> AAA'")
    assert_ch(h, "Mandair", "Mutao", "AAA")


def test_letter_position_with_leading_tag(h: Harness) -> None:
    h.push("Mutao auctions, 'GGG AAA CH <>> Mandair <<> AAA'")
    assert_ch(h, "Mandair", "Mutao", "AAA")


def test_letter_position_without_trailing_copy(h: Harness) -> None:
    h.push("Mutao auctions, 'AAA CH <>> Mandair <<>'")
    assert_ch(h, "Mandair", "Mutao", "AAA")


def test_rch_call_with_tag(h: Harness) -> None:
    h.comms_handler.ch_chain_tag = lambda: "GG"
    h.push("Mutao auctions, 'GG RCH AAA -- TARGET'")
    assert_ch(h, "TARGET", "Mutao", "AAA", tag="GG")


def test_rch_call_with_ch_word(h: Harness) -> None:
    h.push("Mutao tells the group, 'GG RCH BBB CH -- Beefwich'")
    assert_ch(h, "Beefwich", "Mutao", "BBB")


def test_npc_recipient_kept_whole(h: Harness) -> None:
    h.push("Mutao auctions, '007 CH --  a shiverback'")
    assert_ch(h, "a shiverback", "Mutao", "007")


def test_default_position_000(h: Harness) -> None:
    h.push("Mutao tells the group, 'CH >      johny  '")
    assert_ch(h, "johny", "Mutao", "000")


def test_should_warn_of_chain_matrix() -> None:
    """Port of CompleteHealCommsHandlerTests.Parse23."""

    def check(highest: str, position: str, mine: str) -> bool:
        chain = ChainData(highest_order=highest, your_chain_order=mine)
        event = CompleteHealEvent(
            timestamp=T0, recipient="Target", tag="", position=position, caster=""
        )
        return should_warn_of_chain(chain, event)

    assert check("zzz", "aaa", "bbb") is True
    assert check("bbb", "aaa", "bbb") is True
    assert check("ccc", "aaa", "bbb") is True
    assert check("ccc", "ccc", "bbb") is False
    assert check("aaa", "zzz", "bbb") is False
    assert check("bbb", "bbb", "aaa") is False

    assert check("004", "003", "004") is True
    assert check("004", "004", "002") is False
    assert check("004", "001", "002") is True
    assert check("004", "001", "003") is False
    assert check("004", "001", "001") is False
    assert check("004", "004", "001") is True


def test_warning_when_previous_position_calls(h: Harness) -> None:
    h.comms_handler.ch_chain_tag = lambda: "CA"
    h.push("You say out of character, 'CA 002 CH -- Aaryk'")
    assert h.speaker.spoken == []
    h.push("Hanbox says out of character, 'CA 001 CH -- Aaryk'")
    assert h.speaker.spoken == [CH_WARNING_TEXT]
    overlays = h.collector.of_type(OverlayEvent)
    assert overlays and overlays[-1].text == CH_WARNING_TEXT


def test_no_warning_for_other_positions(h: Harness) -> None:
    h.comms_handler.ch_chain_tag = lambda: "CA"
    h.push("You say out of character, 'CA 004 CH -- Aaryk'")
    h.push("Hanbox says out of character, 'CA 001 CH -- Aaryk'")
    assert h.speaker.spoken == []


# -- CH cadence indicator (#15) -----------------------------------------------


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("healers to 4 seconds", 4),
        ("healers to 3", 3),
        ("heals to 5", 5),
        ("chain to 6", 6),
        ("CH to 2", 2),
        ("4 second chain", 4),
        ("3s chain", 3),
        ("HEALERS TO 7 SECONDS", 7),
        ("GG 014 CH -- Wreckognize", None),  # a chain call, not a cadence
        ("healers to 40 people", None),  # out of the [1, 30] range
        ("healers to 0", None),
        ("let's get moving", None),
    ],
)
def test_parse_ch_cadence(content: str, expected: int | None) -> None:
    assert parse_ch_cadence(content) == expected


def _cadence_events(h: Harness) -> list[CompleteHealCadenceEvent]:
    return h.collector.of_type(CompleteHealCadenceEvent)


def test_cadence_published_when_enabled(h: Harness) -> None:
    h.comms_handler.cadence_enabled = lambda: True
    h.push("Raidleader shouts, 'healers to 4 seconds'")
    assert [e.seconds for e in _cadence_events(h)] == [4]


def test_cadence_ignored_when_disabled(h: Harness) -> None:
    # cadence_enabled defaults to False.
    h.push("Raidleader shouts, 'healers to 4 seconds'")
    assert _cadence_events(h) == []


def test_cadence_not_emitted_for_chain_call(h: Harness) -> None:
    h.comms_handler.cadence_enabled = lambda: True
    h.push("Curaja shouts, 'GG 014 CH -- Wreckognize'")
    assert _cadence_events(h) == []
    assert h.collector.of_type(CompleteHealEvent)  # the chain call still parses


def test_parse_ch_cadence_custom_patterns() -> None:
    # A user regex with a first capturing group for the seconds.
    assert parse_ch_cadence("cast every 6 now", patterns=[r"every (\d+)"]) == 6
    # Empty / None falls back to the stock defaults.
    assert parse_ch_cadence("healers to 4 seconds", patterns=[]) == 4
    assert parse_ch_cadence("healers to 4 seconds", patterns=None) == 4


def test_parse_ch_cadence_tolerates_bad_patterns() -> None:
    # An invalid regex is skipped (logged), the valid one still matches.
    assert parse_ch_cadence("healers to 4", patterns=["(unclosed", r"to (\d+)"]) == 4
    # A pattern without a capturing group never crashes.
    assert parse_ch_cadence("healers to 4", patterns=[r"healers"]) is None


def test_cadence_uses_custom_patterns_provider(h: Harness) -> None:
    h.comms_handler.cadence_enabled = lambda: True
    h.comms_handler.cadence_patterns = lambda: [r"cadence (\d+)"]
    h.push("Raidleader shouts, 'cadence 3 go'")
    assert [e.seconds for e in _cadence_events(h)] == [3]
    # The stock "healers to N" phrasing no longer matches once overridden.
    h.push("Raidleader shouts, 'healers to 4 seconds'")
    assert [e.seconds for e in _cadence_events(h)] == [3]
