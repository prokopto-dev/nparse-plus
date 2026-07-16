"""ActivePlayer — mutable state for the currently logged-in character.

Port of EQTool's ViewModels/ActivePlayerInfo.cs, minus the WPF binding.
The persistent per-character profile (PlayerInfo) lives in
``nparseplus.config.settings``; this object tracks the live session and is
re-pointed when the log file switches characters.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from nparseplus.core.enums import PlayerClass, Server

TRACKABLE_CLASSES = frozenset({PlayerClass.DRUID, PlayerClass.RANGER, PlayerClass.BARD})

# PlayerInfo.cs TrackingDistance: skill (?? 10) times a per-class multiplier.
_TRACKING_MULTIPLIER = {
    PlayerClass.RANGER: 24,  # skill * 12 * 2
    PlayerClass.DRUID: 20,  # skill * 10 * 2
    PlayerClass.BARD: 14,  # skill * 7 * 2
}


def tracking_distance(player_class: PlayerClass | None, tracking_skill: int | None) -> float | None:
    """Tracking radius in game units (PlayerInfo.cs:332); None if untrackable.

    EQTool's TrackingSkill is nullable with a ??10 fallback; our stored skill
    defaults to 0, so 0 is treated as unset too.
    """
    if player_class not in _TRACKING_MULTIPLIER:
        return None
    skill = tracking_skill if tracking_skill else 10
    return float(skill * _TRACKING_MULTIPLIER[player_class])


@dataclass
class ActivePlayer:
    name: str = ""
    server: Server | None = None
    level: int | None = None
    player_class: PlayerClass | None = None
    zone: str = ""  # short zone key
    guild_name: str = ""
    tracking_skill: int | None = None
    # Names the session has learned are player characters (from /who etc.).
    known_players: set[str] = field(default_factory=set)

    @property
    def is_configured(self) -> bool:
        return bool(self.name)

    @property
    def server_key(self) -> str | None:
        """The PlayerInfo.server convention: lowercase enum name ("green")."""
        return self.server.name.lower() if self.server is not None else None

    def reset_for(self, name: str, server: Server | None) -> None:
        """Switch to a new character (log file change)."""
        self.name = name
        self.server = server
        self.level = None
        self.player_class = None
        self.zone = ""
        self.guild_name = ""
        self.tracking_skill = None
        self.known_players.clear()
