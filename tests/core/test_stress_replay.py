"""Heavy-combat replay smoke test: the full backend chews through a
synthetic raid log without dropping, reordering, or misparsing events."""

from __future__ import annotations

from collections import Counter
from datetime import timedelta

import pytest
from tests.stress_log import BASE_TIME, MOB, raid_lines

from nparseplus.audio.tts import NullSpeaker
from nparseplus.composition import build_backend
from nparseplus.config.settings import Settings
from nparseplus.core.events import DamageEvent, LineEvent, PlayerLocationEvent

ROUNDS = 500


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
def test_raid_log_replay_counts_and_order() -> None:
    backend = build_backend(Settings(), speaker=NullSpeaker())
    counts: Counter[type] = Counter()
    line_numbers: list[int] = []

    def observe(event: object) -> None:
        counts.update([type(event)])
        if isinstance(event, LineEvent):
            line_numbers.append(event.line_number)

    backend.bus.subscribe_all(observe)

    lines = raid_lines(ROUNDS)
    for raw in lines:
        backend.pipeline.process(raw)

    # Every line surfaced exactly once, in order.
    assert counts[LineEvent] == len(lines)
    assert line_numbers == sorted(line_numbers)
    # 3 hits + 1 miss per round (misses parse as zero-damage DamageEvents,
    # matching EQTool).
    assert counts[DamageEvent] == 4 * ROUNDS
    assert counts[PlayerLocationEvent] == ROUNDS / 10
    # The DPS tracker saw the fight.
    rows = backend.fights.snapshot(BASE_TIME + timedelta(seconds=ROUNDS))
    assert any(row.target_name == MOB for row in rows)
