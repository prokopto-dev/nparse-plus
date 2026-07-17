"""Golden replay: the full backend processes EQtoolsTests' log1.txt and must
produce exactly these typed events. Guards the parser chain, registry order,
and pipeline plumbing as one unit."""

from collections import Counter
from datetime import datetime
from pathlib import Path

import pytest

from nparseplus.audio.tts import NullSpeaker
from nparseplus.composition import build_backend
from nparseplus.config.settings import PlayerInfo, Settings
from nparseplus.core.enums import PlayerClass, Server
from nparseplus.core.events import AfterPlayerChangedEvent
from nparseplus.core.player import tracking_distance

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


def test_who_sequence_updates_active_profile_and_tracking_radius() -> None:
    settings = Settings(players=[PlayerInfo(name="Xantik", server="green", tracking_skill=120)])
    settings.sharing.mode = "off"
    saves: list[None] = []
    backend = build_backend(
        settings,
        speaker=NullSpeaker(),
        request_save=lambda: saves.append(None),
    )
    backend.player.reset_for("Xantik", Server.GREEN)
    backend.bus.publish(AfterPlayerChangedEvent(timestamp=datetime(2026, 7, 17, 12)))
    saves.clear()  # ignore the timer-persistence restore/export save

    prefix = "[Fri Jul 17 12:00:00 2026] "
    for message in (
        "Players on EverQuest:",
        "---------------------------",
        "[54 Hierophant] Xantik (Half Elf) <Bregan D'Aerth>",
        "There are 12 players in Greater Faydark.",
    ):
        backend.pipeline.process(prefix + message)

    profile = settings.players[0]
    assert profile.player_class == int(PlayerClass.DRUID)
    assert profile.level == 54
    assert profile.zone == "gfaydark"
    assert profile.guild_name == "Bregan D'Aerth"
    assert profile.tracking_skill == 120
    assert backend.player.player_class is PlayerClass.DRUID
    assert backend.player.level == 54
    assert backend.player.zone == "gfaydark"
    assert tracking_distance(backend.player.player_class, backend.player.tracking_skill) == 2400.0
    assert len(saves) == 2  # one batched profile save for /who, one for the zone
