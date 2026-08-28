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


class NotableKillEvent(LogEvent):
    """A kill worth copying the fight parse for (#78).

    nparseplus addition, with no counterpart in ``LogEvents.cs`` — EQTool asked
    the question inline in ``DPSMeter.xaml.cs LogParser_DeathEvent`` and copied
    straight from there, which it could do because WPF gave it no thread to
    cross. Here the decision and the clipboard write land on different threads,
    and the decision is the half that must not move: it depends on the zone the
    kill happened in, and ``ActivePlayer.zone`` is mutated on the driver thread
    while the Qt bridge is still buffering events the GUI has not drained. So
    ``DpsHandler`` answers it where the answer is unambiguous and states the
    fact in the ordered stream; the window only decides whether the user asked
    for a copy, and writes one.

    ``zone`` is the short key the kill was judged in, carried so a subscriber
    never has to re-derive it — re-deriving it later is precisely the bug.

    ``parse`` is the finished clipboard line, formatted at the kill for the
    same reason. Zoning out clears the meter (``DpsHandler._on_zoned``), and
    the zone line lands on the driver thread while the bridge is still holding
    the batch — so a boss killed on the way out has no rows left to format by
    the time the GUI wakes up, and a consumer that formatted then would copy
    nothing at all. A parse is a statement about a fight that has ended; there
    is nothing to gain by building it later and a fight to lose.
    """

    victim: str
    zone: str
    parse: str


class DpsBestResetEvent(LogEvent):
    """Outcome of a user-confirmed "Reset best" (#83).

    Not a log fact — a command result, published so it can reach the GUI the
    only way anything on the driver thread may: the bus, and from there
    ``QtEventBridge``. The reset is dispatched to the driver so its identity
    check cannot be overtaken by a character switch, which means the answer is
    only known there, one poll interval or so after the user clicked Yes.

    ``cleared`` is False when the check refused — the active character changed
    between the click and the command being drained. Carried rather than
    publishing only on refusal so there is ONE authoritative path for the
    outcome instead of a fast path and a slow one that can disagree.
    """

    cleared: bool


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
    #: The same-message candidates the guess passed over (#177). Additive on
    #: EventModels.cs, whose SpellCastOnYouEvent carries only the winner: the
    #: C# had nowhere to offer a correction, and the Timers window now does.
    #: Empty whenever the cast message named exactly one spell, which is what
    #: keeps an unambiguous row from growing a menu of alternatives.
    #: SpellCastOnOtherEvent needs no counterpart — it already carries the
    #: whole candidate tuple as ``spells``.
    alternatives: tuple[Spell, ...] = ()


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


class TriggerFiredEvent(LogEvent):
    """nparseplus extension (#31, not in EventModels.cs): a trigger matched a
    line, or one of its timer outputs ran. Feeds the Trigger Editor's Activity
    tab, which is how you tell WHICH trigger of an imported pack went off.

    ``line``/``line_number`` are always the log line that started this: the
    matched line for ``"match"``, and the line that armed the timer for the
    timer phases (they fire from the engine tick, which has no line of its
    own). The output fields carry the token-expanded text that was actually
    shown/spoken — empty when that output was not emitted.
    """

    trigger_id: str
    trigger_name: str = ""
    group: str = ""  # folder/category path (core.triggers.model.trigger_group_key)
    phase: str = "match"  # match | timer_ending | timer_ended | timer_cancelled
    display_text: str = ""
    tts_text: str = ""
    sound_file: str = ""
    timer_name: str = ""  # expanded; empty when this fire involved no timer
    timer_seconds: int = 0
    counter: int = 0  # the trigger's {COUNTER} tally after this match


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


# --- timer events -------------------------------------------------------------


class TimerWindowEvent(RemoteEvent):
    """A variable respawn ("pop") window on a timer row changed phase (#125).

    A ``RemoteEvent`` because these fire from a driver tick and have no source
    log line of their own — the row they describe was armed by one, minutes or
    days ago.

    Plain scalars, deliberately no ``Row`` field: a mutable model inside a
    frozen event is a lie, and it would create an ``events -> timers`` import
    edge that does not exist today. A subscriber that wants the live row calls
    ``timers.find(name, group)``.

    ``opens_at`` / ``closes_at`` are the row's own anchors (its ``ends_at`` and
    ``window_ends_at``), so both events describe the whole window whichever end
    of it you were told about.

    Never published itself — subscribe to one of the two subclasses.
    """

    name: str
    group: str
    opens_at: datetime  # the base end: when the mob became poppable
    closes_at: datetime  # the latest possible pop
    # When the spawn has SEVERAL candidate windows and nobody knows which it
    # will use (#125), these say which one this is: a shared series key and a
    # 1-based position among ``count``. Empty/0 for a lone window, so a
    # subscriber that does not care never has to look.
    series: str = ""
    index: int = 0
    count: int = 0


class TimerWindowOpenedEvent(TimerWindowEvent):
    """The base respawn ran out; the row is now inside its pop window.

    ``opened_at`` is the tick that observed the crossover, which may sit a
    little after ``opens_at`` (the driver polls at 100 ms, and a catch-up read
    of a backlog can cross the boundary long after the fact).
    """

    opened_at: datetime


class TimerWindowClosedEvent(TimerWindowEvent):
    """The latest possible pop passed; the row is gone from the window.

    Fires only for rows that *had* a window, not for every expiry: ``publish``
    dispatches inline and then feeds ``subscribe_all`` -> the Qt bridge's queued
    signal, so a generic per-row expiry event would push a cross-thread signal
    for every buff that drops in a raid.

    ``closed_at`` is the moment the window closed, which is by definition
    ``closes_at`` — ``TimersService.on_expired`` carries rows, not the tick's
    clock, and the row's own anchor is the truthful answer either way.
    """

    closed_at: datetime


# --- character dump events ----------------------------------------------------


class CharacterDumpEvent(RemoteEvent):
    """nparseplus extension (not in EventModels.cs): the dump library took in
    a ``/outputfile`` snapshot for one character.

    These are the hooks the dump library exposes — they carry the identity of
    the snapshot and what changed, not the snapshot itself, so a subscriber
    that only wants to know "did anything happen" costs nothing. Read the
    contents through ``nparseplus.core.dumps.DumpLibrary`` at ``path``.

    Published from the log-driver thread by ``DumpWatcher.tick``, like every
    other bus event, including for imports the user asked for from the window
    (the window hands the request to the watcher rather than doing the work
    itself, precisely so this stays true).
    """

    character: str
    kind: str  # DumpKind value: "inventory" | "spellbook"
    server: str = ""
    captured_at: datetime
    entry_count: int = 0
    digest: str = ""
    path: str = ""  # the stored snapshot, not the game's file
    source_file: str = ""


class CharacterDumpImportedEvent(CharacterDumpEvent):
    """The first snapshot of this character and kind entered the library."""


class CharacterDumpUpdatedEvent(CharacterDumpEvent):
    """A tracked dump changed and a new snapshot was stored.

    ``added``/``removed`` are entry names diffed as a multiset against the
    previous snapshot — item names for an inventory, spell names for a book.
    """

    added: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()
