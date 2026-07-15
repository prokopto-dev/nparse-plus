"""GroupLeaderHandler — tracks the current group leader's name.

Port of EQTool's Services/Handlers/GroupLeaderHandler.cs. The C# writes to
SettingsWindowViewModel.GroupLeaderName; the Qt-free equivalent keeps the
name here with a change hook for the UI to observe.
"""

from __future__ import annotations

from collections.abc import Callable

from nparseplus.core.bus import EventBus
from nparseplus.core.events import GroupLeaderEvent, WelcomeEvent
from nparseplus.core.handlers.base import BaseHandler
from nparseplus.core.player import ActivePlayer

NO_LEADER = "None"


class GroupLeaderHandler(BaseHandler):
    def __init__(self, bus: EventBus, player: ActivePlayer) -> None:
        super().__init__(bus, player)
        self.group_leader_name = NO_LEADER
        self.on_change: list[Callable[[str], None]] = []
        bus.subscribe(GroupLeaderEvent, self._on_group_leader)
        bus.subscribe(WelcomeEvent, self._on_welcome)

    def _set(self, name: str) -> None:
        self.group_leader_name = name
        for callback in list(self.on_change):
            callback(name)

    def _on_group_leader(self, event: GroupLeaderEvent) -> None:
        self._set(event.group_leader_name)

    def _on_welcome(self, event: WelcomeEvent) -> None:
        # Ensure the leader is cleared upon login.
        self._set(NO_LEADER)
