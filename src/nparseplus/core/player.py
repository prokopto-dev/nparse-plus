"""ActivePlayer — mutable state for the currently logged-in character.

Port of EQTool's ViewModels/ActivePlayerInfo.cs, minus the WPF binding.
The persistent per-character profile (PlayerInfo) lives in
``nparseplus.config.settings``; this object tracks the live session and is
re-pointed when the log file switches characters.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from nparseplus.core.enums import PlayerClass, Server


@dataclass
class ActivePlayer:
    name: str = ""
    server: Server | None = None
    level: int | None = None
    player_class: PlayerClass | None = None
    zone: str = ""  # short zone key
    guild_name: str = ""
    # Names the session has learned are player characters (from /who etc.).
    known_players: set[str] = field(default_factory=set)

    @property
    def is_configured(self) -> bool:
        return bool(self.name)

    def reset_for(self, name: str, server: Server | None) -> None:
        """Switch to a new character (log file change)."""
        self.name = name
        self.server = server
        self.level = None
        self.player_class = None
        self.zone = ""
        self.guild_name = ""
        self.known_players.clear()
