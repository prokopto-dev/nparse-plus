"""PlayerProfileHandler — profile <-> ActivePlayer sync (C# handler parity)."""

from datetime import datetime

from nparseplus.config.settings import PlayerInfo, Settings, get_player
from nparseplus.core.bus import EventBus
from nparseplus.core.enums import PlayerClass, Server
from nparseplus.core.events import (
    AfterPlayerChangedEvent,
    ClassDetectedEvent,
    PlayerLevelDetectionEvent,
    WhoPlayer,
    WhoPlayerEvent,
    YouZonedEvent,
)
from nparseplus.core.handlers.player_profile import PlayerProfileHandler
from nparseplus.core.player import ActivePlayer

T0 = datetime(2026, 7, 8, 12, 0, 0)


class Rig:
    def __init__(self, profile: PlayerInfo | None = None) -> None:
        self.settings = Settings()
        if profile is not None:
            self.settings.players.append(profile)
        self.bus = EventBus()
        self.player = ActivePlayer()
        self.player.reset_for("Xantik", Server.GREEN)
        self.saves = 0
        PlayerProfileHandler(self.bus, self.player, self.settings, request_save=self._count_save)

    def _count_save(self) -> None:
        self.saves += 1


def test_player_changed_loads_saved_profile() -> None:
    profile = PlayerInfo(
        name="Xantik",
        server="green",
        player_class=int(PlayerClass.DRUID),
        level=54,
        zone="gfaydark",
        tracking_skill=125,
        guild_name="Bregan D'Aerth",
    )
    rig = Rig(profile)
    rig.bus.publish(AfterPlayerChangedEvent(timestamp=T0))
    assert rig.player.player_class is PlayerClass.DRUID
    assert rig.player.level == 54
    assert rig.player.zone == "gfaydark"
    assert rig.player.tracking_skill == 125
    assert rig.player.guild_name == "Bregan D'Aerth"


def test_player_changed_without_profile_creates_none_and_keeps_defaults() -> None:
    rig = Rig()
    rig.bus.publish(AfterPlayerChangedEvent(timestamp=T0))
    assert rig.player.player_class is None
    assert rig.player.level is None
    # get_player in _profile creates the row lazily only when events land;
    # the changed event itself created one for future persistence.
    assert len(rig.settings.players) == 1


def test_class_detected_fills_unset_class_and_persists() -> None:
    rig = Rig()
    rig.bus.publish(ClassDetectedEvent(timestamp=T0, player_class=PlayerClass.DRUID))
    assert rig.player.player_class is PlayerClass.DRUID
    info = get_player(rig.settings, "Xantik", "green")
    assert info.player_class == int(PlayerClass.DRUID)
    assert rig.saves == 1


def test_class_detected_never_overwrites() -> None:
    # PlayerClassDetectedHandler.cs: only fills when PlayerClass is unset.
    profile = PlayerInfo(name="Xantik", server="green", player_class=int(PlayerClass.CLERIC))
    rig = Rig(profile)
    rig.bus.publish(AfterPlayerChangedEvent(timestamp=T0))
    rig.bus.publish(ClassDetectedEvent(timestamp=T0, player_class=PlayerClass.DRUID))
    assert rig.player.player_class is PlayerClass.CLERIC
    assert profile.player_class == int(PlayerClass.CLERIC)
    assert rig.saves == 0


def test_level_detection_only_raises() -> None:
    # PlayerLevelDetectionHandler.cs: Level only ever goes up.
    profile = PlayerInfo(name="Xantik", server="green", level=30)
    rig = Rig(profile)
    rig.bus.publish(AfterPlayerChangedEvent(timestamp=T0))
    rig.bus.publish(PlayerLevelDetectionEvent(timestamp=T0, player_level=29))
    assert rig.player.level == 30 and rig.saves == 0
    rig.bus.publish(PlayerLevelDetectionEvent(timestamp=T0, player_level=31))
    assert rig.player.level == 31
    assert profile.level == 31
    assert rig.saves == 1


def test_zone_change_persists_into_profile() -> None:
    profile = PlayerInfo(name="Xantik", server="green", zone="gfaydark")
    rig = Rig(profile)
    rig.bus.publish(
        YouZonedEvent(timestamp=T0, long_name="east commonlands", short_name="ecommons")
    )
    assert profile.zone == "ecommons"
    assert rig.saves == 1
    # Same zone again: no redundant save.
    rig.bus.publish(
        YouZonedEvent(timestamp=T0, long_name="east commonlands", short_name="ecommons")
    )
    assert rig.saves == 1


def test_own_who_row_authoritatively_updates_profile() -> None:
    profile = PlayerInfo(
        name="Xantik",
        server="green",
        player_class=int(PlayerClass.CLERIC),
        level=55,
    )
    rig = Rig(profile)
    rig.bus.publish(
        WhoPlayerEvent(
            timestamp=T0,
            player=WhoPlayer(
                name="xantik",
                player_class=PlayerClass.DRUID,
                level=54,
                guild_name="Bregan D'Aerth",
            ),
        )
    )
    assert rig.player.player_class is PlayerClass.DRUID
    assert rig.player.level == 54  # /who also catches a level lost to death
    assert rig.player.guild_name == "Bregan D'Aerth"
    assert profile.player_class == int(PlayerClass.DRUID)
    assert profile.level == 54
    assert profile.guild_name == "Bregan D'Aerth"
    assert rig.saves == 1


def test_other_players_who_rows_do_not_change_active_profile() -> None:
    profile = PlayerInfo(name="Xantik", server="green", level=54)
    rig = Rig(profile)
    rig.bus.publish(
        WhoPlayerEvent(
            timestamp=T0,
            player=WhoPlayer(name="Someoneelse", player_class=PlayerClass.DRUID, level=60),
        )
    )
    assert rig.player.player_class is None
    assert rig.player.level is None
    assert profile.level == 54
    assert rig.saves == 0


def test_no_server_is_inert() -> None:
    rig = Rig()
    rig.player.reset_for("Xantik", None)
    rig.bus.publish(ClassDetectedEvent(timestamp=T0, player_class=PlayerClass.DRUID))
    # Live player still updated (class detection is session state) but no
    # profile row/save without a server key.
    assert rig.player.player_class is PlayerClass.DRUID
    assert rig.settings.players == []
    assert rig.saves == 0
