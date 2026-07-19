"""Typed log events — 1:1 port of EQTool's LogEvents.cs / EventModels.cs.

Every event carries the source line's timestamp, text, and line number
(``LogEvent`` base). Remote/UI events that don't originate from a log line
derive from plain ``BaseModel`` instead, mirroring the C# split between
``BaseLogParseEvent`` and the remote/overlay event classes.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from nparseplus.core.enums import (
    CommsChannel,
    FactionStatus,
    PetIncident,
    PlayerClass,
)
from nparseplus.core.geometry import Loc
from nparseplus.core.spells.models import Spell


class LogEvent(BaseModel):
    """Base for events raised from a parsed log line (BaseLogParseEvent)."""

    model_config = ConfigDict(frozen=True)

    timestamp: datetime
    line: str = ""
    line_number: int = 0


# --- simple marker events (payload is just the base fields) -----------------


class ExpGainedEvent(LogEvent): ...


class CampEvent(LogEvent): ...


class QuakeEvent(LogEvent): ...


class RingWarEvent(LogEvent): ...


class LoadingPleaseWaitEvent(LogEvent): ...


class WelcomeEvent(LogEvent): ...


class WhoEvent(LogEvent): ...


class BeforePlayerChangedEvent(LogEvent): ...


class AfterPlayerChangedEvent(LogEvent): ...


class YourSpellInterruptedEvent(LogEvent): ...


class MendWoundsEvent(LogEvent): ...


class LineEvent(LogEvent):
    """Raw line firehose — published for every line after the parser chain."""


# --- payload events ----------------------------------------------------------


class FactionEvent(LogEvent):
    faction: str
    status: FactionStatus


class PlayerLocationEvent(LogEvent):
    location: Loc


class BoatEvent(LogEvent):
    boat: str  # short boat key from data/zones.json boats table
    start_point: str = ""


class FTEEvent(LogEvent):
    npc_name: str
    fte_person: str


class DamageEvent(LogEvent):
    target_name: str
    attacker_name: str
    damage_done: int
    damage_type: str
    level_guess: int | None = None


class ConEvent(LogEvent):
    name: str


class SlainEvent(LogEvent):
    victim: str
    killer: str = ""


class ConfirmedDeathEvent(LogEvent):
    victim: str
    killer: str = ""


class WhoPlayer(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    level: int | None = None
    player_class: PlayerClass | None = None
    guild_name: str | None = None


class WhoPlayerEvent(LogEvent):
    player: WhoPlayer


class CommsEvent(LogEvent):
    channel: CommsChannel
    content: str
    sender: str = ""
    receiver: str = ""


class DisciplineCooldownEvent(LogEvent):
    discipline_name: str
    total_timer_seconds: int


class PetEvent(LogEvent):
    incident: PetIncident
    pet_name: str = ""


class GroupLeaderEvent(LogEvent):
    group_leader_name: str = ""


class YouBeginCastingEvent(LogEvent):
    spell: Spell


class YouFinishCastingEvent(LogEvent):
    spell: Spell
    target_name: str = ""


class SpellCastOnYouEvent(LogEvent):
    spell: Spell


class SpellCastOnOtherEvent(LogEvent):
    spells: tuple[Spell, ...]
    target_name: str


class ResistSpellEvent(LogEvent):
    spell: Spell
    is_you: bool


class ClassDetectedEvent(LogEvent):
    player_class: PlayerClass


class PlayerLevelDetectionEvent(LogEvent):
    player_level: int


class RandomRollEvent(LogEvent):
    player_name: str
    max_roll: int
    roll: int


class CompleteHealEvent(LogEvent):
    recipient: str
    tag: str
    position: str
    caster: str


class CompleteHealCadenceEvent(LogEvent):
    """A raid-leader CH cadence call ("healers to 4 seconds"), #15.

    ``seconds`` is the declared interval between chained casts. nparseplus
    extension (no EQTool equivalent); only published when the opt-in
    ``ch_cadence_indicator`` setting is on.
    """

    seconds: int


class YouHaveFinishedMemorizingEvent(LogEvent):
    spell_name: str


class YouForgetEvent(LogEvent):
    spell_name: str


class DragonRoarEvent(LogEvent):
    spell: Spell


class SpellWornOffSelfEvent(LogEvent):
    spell_names: tuple[str, ...]


class SpellWornOffOtherEvent(LogEvent):
    spell_name: str


class YourItemBeginsToGlowEvent(LogEvent):
    item_name: str


class YouZonedEvent(LogEvent):
    long_name: str
    short_name: str


# --- remote (network) events — not from log lines ----------------------------


class RemoteEvent(BaseModel):
    model_config = ConfigDict(frozen=True)


class DragonRoarRemoteEvent(RemoteEvent):
    spell_name: str
    # Roar location in wire (raw /loc) order, like RemotePlayer — the map
    # adapter owns the transform. None unless the sender knew all of X/Y/Z.
    location: Loc | None = None
    server: int | None = None


class CustomTimerReceivedRemoteEvent(RemoteEvent):
    """SignalrCustomTimer pushed by the PigParse server (Kael pull timers)."""

    name: str
    duration_in_seconds: int
    spell_name_icon: str | None = None
    server: int | None = None


class RemotePlayer(BaseModel):
    """Wire shape of a shared player (SignalrPlayerV2 subset)."""

    model_config = ConfigDict(frozen=True)

    name: str
    server: int | None = None
    zone: str = ""
    guild_name: str | None = None
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    tracking_distance: float | None = None


class PlayerDisconnectReceivedRemoteEvent(RemoteEvent):
    player: RemotePlayer


class OtherPlayerLocationReceivedRemoteEvent(RemoteEvent):
    player: RemotePlayer


class RemoteWaypoint(BaseModel):
    """A shared map waypoint from an nparse-wire state snapshot. Coordinates
    are in raw ``/loc`` print order, like RemotePlayer."""

    model_config = ConfigDict(frozen=True)

    key: str  # server snapshot key ("Player:expiry")
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    icon: str = "corpse"


class WaypointsReceivedRemoteEvent(RemoteEvent):
    """nparseplus port of the original nparse waypoint feed: the full waypoint
    snapshot for one zone (the maps window reconciles add/remove against it)."""

    zone: str  # short zone key
    waypoints: tuple[RemoteWaypoint, ...] = ()


# --- UI/overlay events --------------------------------------------------------


class OverlayEvent(RemoteEvent):
    text: str
    foreground: str = ""  # color token; UI resolves to a brush
    reset: bool = False
    # Which overlay region renders this alert. "alert" (default) is the center
    # text; "utility" routes to the dedicated utility header section (#14).
    # Deliberate nparseplus divergence from EQTool (no per-alert region there).
    section: str = "alert"


class TimerBarEvent(RemoteEvent):
    name: str
    total_seconds: int
    bar_color: str | None = None


class CorpseMarkerEvent(LogEvent):
    """nparseplus extension (original-nparse corpse waypoints): you died at a
    known location; the maps window marks it and the coordinator shares it."""

    name: str  # the character's (share) name
    zone: str  # short zone key
    loc: Loc


class WindowCommandEvent(LogEvent):
    """nparseplus extension (nparse #42/#64, not in EventModels.cs): the player
    typed show_/hide_/toggle_<window> in chat; app.py flips the window."""

    window: str
    action: str  # "show" | "hide" | "toggle"
