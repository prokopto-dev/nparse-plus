"""SharingCoordinator — the single gate between the engine and the network.

Ports the policy half of EQTool's Services/SignalrPlayerHub.cs. The
transport half lives in ``nparseplus.net`` behind the ``SharingClient``
protocol; this module never imports ``net``.

Threading contract (the bus and TimersService are NOT thread-safe):

* Outbound: bus subscriptions and ``tick`` run on the driver thread and may
  call the client freely — client sends are non-blocking enqueues/drops.
* Inbound: net threads call ``enqueue_inbound`` (thread-safe) ONLY. The
  driver-thread ``tick`` drains the inbox and is the sole place inbound
  traffic touches the bus or timers.

Gating: this class is the only reader of ``settings.sharing.mode`` and the
per-character ``PlayerInfo.map_location_sharing`` / ``share_timers`` prefs.

EQTool cadence (do not exceed — shared service courtesy): immediate send on
every parsed ``/loc``, 10 s keepalive resend, keepalive stops after 5 idle
minutes or a camp; dragon roars dedupe same-spell within 4 s.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from queue import Empty, SimpleQueue
from typing import TYPE_CHECKING, Protocol

from nparseplus.config.settings import find_player
from nparseplus.core.enums import MapLocationSharing
from nparseplus.core.events import (
    CampEvent,
    CorpseMarkerEvent,
    CustomTimerReceivedRemoteEvent,
    DragonRoarEvent,
    DragonRoarRemoteEvent,
    OtherPlayerLocationReceivedRemoteEvent,
    PlayerDisconnectReceivedRemoteEvent,
    PlayerLocationEvent,
    RemotePlayer,
    WaypointsReceivedRemoteEvent,
)
from nparseplus.core.geometry import Loc
from nparseplus.core.player import tracking_distance
from nparseplus.core.timers import TRIGGER_TIMER_GROUP, TimerRow

if TYPE_CHECKING:
    from nparseplus.config.settings import PlayerInfo, Settings
    from nparseplus.core.bus import EventBus
    from nparseplus.core.player import ActivePlayer
    from nparseplus.core.timers import TimersService

logger = logging.getLogger(__name__)

KEEPALIVE_SECONDS = 10.0
IDLE_LIMIT_SECONDS = 300.0  # 5 min without a "You..." line stops keepalives
ROAR_DEDUPE_SECONDS = 4.0
ROAR_PRUNE_SECONDS = 45.0

_SHARING_WIRE = {
    "everyone": MapLocationSharing.EVERYONE,
    "guild": MapLocationSharing.GUILD_ONLY,
    # "off" is a nparseplus extension (EQTool cannot turn map sharing off);
    # it gates location sends entirely, but roars still carry a value.
    "off": MapLocationSharing.EVERYONE,
}


class SharingClient(Protocol):
    """What the coordinator needs from a network client (pigparse or nparse)."""

    @property
    def status(self) -> str: ...
    def start(self) -> None: ...
    def stop(self) -> None: ...
    def set_server(self, server: int | None) -> None: ...
    def send_location(
        self,
        *,
        name: str,
        guild_name: str | None,
        server: int,
        zone: str,
        sharing: int,
        loc: Loc,
        tracking_distance: float | None = None,
    ) -> None: ...
    def send_dragon_roar(
        self,
        *,
        spell_name: str,
        guild_name: str | None,
        server: int,
        zone: str,
        sharing: int,
        loc: Loc | None,
    ) -> None: ...
    def send_waypoint(
        self,
        *,
        name: str,
        zone: str,
        loc: Loc,
        icon: str = "corpse",
        timeout_minutes: int = 60,
    ) -> None: ...


class SharingCoordinator:
    def __init__(
        self,
        bus: EventBus,
        player: ActivePlayer,
        settings: Settings,
        timers: TimersService,
        last_you_activity: Callable[[], datetime | None],
        client: SharingClient | None = None,
    ) -> None:
        self.bus = bus
        self.player = player
        self.settings = settings
        self.timers = timers
        self._last_you_activity = last_you_activity
        self._client = client
        self._inbox: SimpleQueue[object] = SimpleQueue()
        self._last_loc: Loc | None = None
        self._last_send_time: datetime | None = None
        self._recent_roars: list[tuple[str, datetime]] = []
        self._client_server: int | None = None
        bus.subscribe(PlayerLocationEvent, self._on_location)
        bus.subscribe(CampEvent, self._on_camp)
        bus.subscribe(DragonRoarEvent, self._on_dragon_roar)
        bus.subscribe(CorpseMarkerEvent, self._on_corpse_marker)

    def set_client(self, client: SharingClient | None) -> None:
        self._client = client

    @property
    def status(self) -> str:
        mode = self.settings.sharing.mode
        if mode == "off" or self._client is None:
            return "off"
        return f"{mode} — {self._client.status}"

    # --- inbound (any thread enqueues; only tick drains) -----------------------

    def enqueue_inbound(self, item: object) -> None:
        self._inbox.put(item)

    # --- driver-thread entry points ---------------------------------------------

    def tick(self, now: datetime) -> None:
        self._sync_server()
        self._drain_inbox(now)
        self._keepalive(now)

    def _sync_server(self) -> None:
        server = int(self.player.server) if self.player.server is not None else None
        if self._client is not None and server != self._client_server:
            self._client_server = server
            self._client.set_server(server)

    def _drain_inbox(self, now: datetime) -> None:
        while True:
            try:
                item = self._inbox.get_nowait()
            except Empty:
                return
            try:
                self._dispatch_inbound(item, now)
            except Exception:
                logger.exception("sharing inbound dispatch failed for %r", type(item).__name__)

    def _dispatch_inbound(self, item: object, now: datetime) -> None:
        if callable(item):
            # A NetWorker delivery: apply a fetched result on the driver thread.
            item()
            return
        if isinstance(
            item, OtherPlayerLocationReceivedRemoteEvent | PlayerDisconnectReceivedRemoteEvent
        ):
            if self._is_self_echo(item.player):
                return
            self.bus.publish(item)
            return
        if isinstance(item, WaypointsReceivedRemoteEvent):
            # Deliberately NOT self-echo-filtered: your own corpse marker comes
            # back keyed "Name:expiry" and must render like everyone else's.
            self.bus.publish(item)
            return
        if isinstance(item, DragonRoarRemoteEvent):
            # C# acts only on same-server roars when this character shares
            # timers. (At the pinned commit nothing in EQTool subscribes to
            # the remote roar — publish-only, no local timer effect.)
            info = self._player_info()
            share_timers = info.share_timers if info is not None else True
            my_server = self._my_server_int()
            if my_server is not None and item.server == my_server and share_timers:
                self.bus.publish(item)
            return
        if isinstance(item, CustomTimerReceivedRemoteEvent):
            if self._my_server_int() is None or item.server != self._my_server_int():
                return
            self._add_remote_custom_timer(item, now)
            self.bus.publish(item)
            return
        logger.debug("sharing inbound item ignored: %r", type(item).__name__)

    def _is_self_echo(self, remote: RemotePlayer) -> bool:
        """The servers echo our own frames back; drop name+server matches
        (case-sensitive, like SignalrPlayerHub.cs). The nparse wire carries
        no server (None) — there a name match alone is the echo."""
        if remote.name != self._share_name():
            return False
        return remote.server is None or remote.server == self._my_server_int()

    def _my_server_int(self) -> int | None:
        return int(self.player.server) if self.player.server is not None else None

    def _add_remote_custom_timer(self, item: CustomTimerReceivedRemoteEvent, now: datetime) -> None:
        """Port of SignalrPlayerHub.CustomTriggerReceived: one shared timer
        row, restarted if it already exists. (The C# also colors the Kael
        pull row LightPink and falls back to the Feign Death icon; our rows
        carry no color/icon — the spell window styles the group.)"""
        ends_at = now + timedelta(seconds=item.duration_in_seconds)
        existing = self.timers.find(item.name, TRIGGER_TIMER_GROUP)
        if isinstance(existing, TimerRow):
            existing.ends_at = ends_at
            existing.updated_at = now
            return
        self.timers.add_timer(
            TimerRow(
                name=item.name,
                group=TRIGGER_TIMER_GROUP,
                updated_at=now,
                ends_at=ends_at,
                total_duration_s=float(item.duration_in_seconds),
            )
        )

    def _keepalive(self, now: datetime) -> None:
        if self._last_loc is None or self._last_send_time is None:
            return
        if (now - self._last_send_time).total_seconds() < KEEPALIVE_SECONDS:
            return
        last_activity = self._last_you_activity()
        if last_activity is None or (now - last_activity).total_seconds() > IDLE_LIMIT_SECONDS:
            # EQTool: LastPlayer = null — stop resending until the next /loc.
            self._last_loc = None
            self._last_send_time = None
            return
        self._send_location(self._last_loc, now)

    # --- outbound (driver thread via bus) -----------------------------------------

    def _on_location(self, event: PlayerLocationEvent) -> None:
        if not self._location_sharing_allowed():
            return
        self._send_location(event.location, event.timestamp)

    def _on_camp(self, _event: CampEvent) -> None:
        self._last_loc = None
        self._last_send_time = None

    def _on_dragon_roar(self, event: DragonRoarEvent) -> None:
        # C# gates: player known, zone non-blank, ShareTimers, server known.
        client = self._client
        info = self._player_info()
        if (
            client is None
            or self.settings.sharing.mode == "off"
            or self.player.server is None
            or not self.player.zone
            or not (info.share_timers if info is not None else True)
        ):
            return
        spell_name = event.spell.name
        cutoff = event.timestamp
        if any(
            name == spell_name and (cutoff - when).total_seconds() < ROAR_DEDUPE_SECONDS
            for name, when in self._recent_roars
        ):
            return
        # C# removes prior entries of the same spell, then prunes >45s.
        self._recent_roars = [
            (name, when)
            for name, when in self._recent_roars
            if name != spell_name and (cutoff - when).total_seconds() <= ROAR_PRUNE_SECONDS
        ]
        self._recent_roars.append((spell_name, cutoff))
        client.send_dragon_roar(
            spell_name=spell_name,
            guild_name=self.player.guild_name or None,
            server=int(self.player.server),
            zone=self.player.zone,
            sharing=int(self._sharing_wire_value()),
            loc=self._last_loc,
        )

    def _on_corpse_marker(self, event: CorpseMarkerEvent) -> None:
        if not self._location_sharing_allowed():
            return
        client = self._client
        if client is None:
            return
        client.send_waypoint(
            name=self._share_name(),
            zone=event.zone,
            loc=event.loc,
            icon="corpse",
            timeout_minutes=60,  # the original client's fixed corpse timeout
        )

    def _send_location(self, loc: Loc, when: datetime) -> None:
        client = self._client
        if client is None or self.player.server is None:
            return
        client.send_location(
            name=self._share_name(),
            guild_name=self.player.guild_name or None,
            server=int(self.player.server),
            zone=self.player.zone,
            sharing=int(self._sharing_wire_value()),
            loc=loc,
            tracking_distance=tracking_distance(
                self.player.player_class, self.player.tracking_skill
            ),
        )
        self._last_loc = loc
        self._last_send_time = when

    # --- the one gate ---------------------------------------------------------------

    def _location_sharing_allowed(self) -> bool:
        if self.settings.sharing.mode == "off" or self._client is None:
            return False
        if self.player.server is None or not self.player.name:
            return False
        info = self._player_info()
        return (info.map_location_sharing if info is not None else "everyone") != "off"

    def _share_name(self) -> str:
        return self.settings.sharing.player_name_override or self.player.name

    def _sharing_wire_value(self) -> MapLocationSharing:
        info = self._player_info()
        pref = info.map_location_sharing if info is not None else "everyone"
        return _SHARING_WIRE[pref]

    def _player_info(self) -> PlayerInfo | None:
        """The persistent per-character profile, if one exists (read-only)."""
        server_key = self.player.server_key
        if server_key is None:
            return None
        return find_player(self.settings, self.player.name, server_key)
