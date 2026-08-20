"""The benchmark fixtures have to be real traffic, and this is what says so.

Deliberately NOT marked ``benchmark``: it runs in the default suite. A
benchmark suite whose input silently stopped matching any parser would keep
producing beautiful, meaningless numbers — nothing else in the repo would
notice, because no assertion depends on those lines. So the guard is here:
every profile must parse, must reach a spread of parsers, and must arrive at
a plausible rate for the situation it claims to describe.
"""

from __future__ import annotations

from collections import Counter

import pytest
from tests.perf.profiles import PROFILES, corpus_lines, group_lines, profile, raid_lines, solo_lines

from nparseplus.audio.tts import NullSpeaker
from nparseplus.composition import build_backend
from nparseplus.config.settings import Settings
from nparseplus.core.lineinfo import parse_line

SECONDS = 60


def _replay(lines: list[str]) -> Counter[str]:
    backend = build_backend(Settings(), speaker=NullSpeaker())
    counts: Counter[str] = Counter()
    backend.bus.subscribe_all(lambda event: counts.update([type(event).__name__]))
    for raw in lines:
        backend.pipeline.process(raw)
    return counts


@pytest.mark.parametrize("name", sorted(PROFILES))
def test_every_line_has_a_parseable_timestamp(name: str) -> None:
    """``parse_line`` falling back to ``datetime.now()`` would hide a typo."""
    for index, raw in enumerate(profile(name, SECONDS), start=1):
        info = parse_line(raw, index)
        assert info is not None, raw
        assert info.timestamp.year == 2026, raw


@pytest.mark.parametrize("name", sorted(PROFILES))
def test_profiles_are_deterministic(name: str) -> None:
    assert profile(name, SECONDS) == profile(name, SECONDS)


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
@pytest.mark.parametrize(
    ("name", "expected"),
    [
        # What each profile is FOR, as an assertion. Solo is movement and one
        # mob; group adds a pet and party kills; raid adds the CH chain.
        ("solo", {"DamageEvent", "PlayerLocationEvent", "SlainEvent", "YouBeginCastingEvent"}),
        ("group", {"DamageEvent", "PetEvent", "CommsEvent", "SlainEvent", "ExpGainedEvent"}),
        ("raid", {"DamageEvent", "CompleteHealEvent", "CommsEvent", "FTEEvent"}),
    ],
)
def test_profiles_reach_the_parsers_they_claim_to(name: str, expected: set[str]) -> None:
    counts = _replay(profile(name, SECONDS))
    assert expected <= set(counts), sorted(set(counts))


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
@pytest.mark.parametrize("name", sorted(PROFILES))
def test_most_lines_produce_a_domain_event(name: str) -> None:
    """A fixture that mostly falls through the chain measures the wrong thing.

    ``LineEvent`` fires for every line whatever happens, so it is excluded:
    what this counts is lines a parser actually claimed.
    """
    lines = profile(name, SECONDS)
    counts = _replay(lines)
    claimed = sum(count for event, count in counts.items() if event != "LineEvent")
    assert claimed > len(lines) * 0.7, dict(counts)


@pytest.mark.parametrize(
    ("fn", "low", "high"),
    [
        # Lines per in-game second, bracketing what each situation produces.
        # Wide brackets on purpose: these guard against a profile silently
        # becoming a different profile, not against tuning.
        (solo_lines, 2.0, 5.0),
        (group_lines, 5.0, 12.0),
        (raid_lines, 15.0, 35.0),
    ],
)
def test_line_rates_match_the_situation_described(fn, low: float, high: float) -> None:
    rate = len(fn(SECONDS)) / SECONDS
    assert low <= rate <= high, rate


def test_the_corpus_is_the_capture_the_replay_test_uses() -> None:
    """Same file, so a fixture change shows up in both places at once."""
    lines = corpus_lines()
    assert len(lines) == 93
    assert "Your Location is" in lines[0]
