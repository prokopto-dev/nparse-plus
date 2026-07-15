"""Golden replay: the full backend processes EQtoolsTests' log1.txt and must
produce exactly these typed events. Guards the parser chain, registry order,
and pipeline plumbing as one unit."""

from collections import Counter
from pathlib import Path

import pytest

from nparseplus.audio.tts import NullSpeaker
from nparseplus.composition import build_backend
from nparseplus.config.settings import Settings

FIXTURE = Path(__file__).parent.parent / "fixtures" / "logfiles" / "log1.txt"

EXPECTED = {
    "ClassDetectedEvent": 1,
    "CommsEvent": 10,
    "LineEvent": 93,  # every parseable line, including the BOM-prefixed first one
    "LoadingPleaseWaitEvent": 1,
    "PlayerLevelDetectionEvent": 1,
    "PlayerLocationEvent": 41,
    "SpellWornOffSelfEvent": 33,
    "WelcomeEvent": 1,
    "YouHaveFinishedMemorizingEvent": 1,
    "YouZonedEvent": 2,
}


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
def test_log1_replay_event_counts() -> None:
    backend = build_backend(Settings(), speaker=NullSpeaker())
    counts: Counter[str] = Counter()
    backend.bus.subscribe_all(lambda event: counts.update([type(event).__name__]))

    with FIXTURE.open(encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            backend.pipeline.process(raw)

    assert dict(counts) == EXPECTED
