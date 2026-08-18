"""Pydantic settings models and JSON persistence for nParse+.

Plain ``BaseModel`` subclasses (not ``BaseSettings``): the whole tree is
persisted as a single human-editable ``settings.json`` written atomically.
No Qt imports allowed in this layer.
"""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from nparseplus.config.paths import ensure_config_dir, settings_path
from nparseplus.core.ch_chain import DEFAULT_CH_CADENCE_PATTERNS

# Triggers persist in the engine's own (Qt-free) schema — one model, no drift.
from nparseplus.core.triggers.model import Trigger

SCHEMA_VERSION = 1

# Safe-mode kill switch: skip all plugin loading regardless of the setting.
NO_PLUGINS_ENV_VAR = "NPARSEPLUS_NO_PLUGINS"


def _default_eq_log_dir() -> Path:
    return Path.home() / "Games/EverQuest/Logs"


# Smallest an overlay region may be stored at — the same two numbers
# ``ui/eventoverlay.py`` drags against, and it imports them from here rather
# than the other way round: this layer is the one that has to police what a
# settings.json says, and it may not import Qt to find out (#107).
MIN_REGION_WIDTH = 120
MIN_REGION_HEIGHT = 32


class OverlayRegion(BaseModel):
    """Per-region placement for the event overlay's three zones.

    ``anchor`` picks the vertical reference line inside the overlay window
    (top margin / vertical center / bottom margin); ``dx``/``dy`` nudge the
    region off that anchor (px, +x right / +y down). ``width`` overrides the
    region's default host width (None = the region's built-in default).

    ``height`` is a FLOOR, not a cap: content taller than it still grows the
    region rather than being cut off. The Alerts region is the one that reads
    it as a budget as well — the headline is shrunk (and past the floor size,
    scrolled) to fit the height you gave it (#102).
    """

    anchor: Literal["top", "center", "bottom"] = "top"
    dx: int = 0
    dy: int = 0
    width: int | None = Field(default=None, ge=MIN_REGION_WIDTH)
    height: int | None = Field(default=None, ge=MIN_REGION_HEIGHT)

    @model_validator(mode="before")
    @classmethod
    def _repair_unusable_sizes(cls, data: object) -> object:
        """Bring a stored size back inside the bounds instead of rejecting it.

        A region small enough to hold nothing renders nothing, silently: the
        Alerts region reads its height as an exact text budget, so a legacy
        or hand-edited ``"height": 8`` left the headline a couple of pixels
        tall with no error anywhere (#107). Too-small is therefore clamped UP
        to the floor — that keeps the user's "I want it small" — while a value
        that is not a number at all is dropped to ``None``, the region's own
        "use the default".

        Never raises, which is the whole reason this is a validator and not
        just the ``ge=`` bounds above: ``load_settings`` reads a ValueError as
        a corrupt document and falls back to defaults, so one bad pixel count
        would discard every other setting in the file. Same shape as
        ``PluginsSettings``, which normalizes or drops an unusable registry
        row rather than failing the load. The bounds then still hold the line
        for code constructing a region directly.

        The floors here are structural, not sufficient: what the Alerts region
        actually needs depends on the font and skin, so the overlay clamps it
        again against real chrome once those are known.
        """
        if not isinstance(data, Mapping):
            return data
        repaired = dict(data)
        for key, floor in (("width", MIN_REGION_WIDTH), ("height", MIN_REGION_HEIGHT)):
            value = repaired.get(key)
            if value is None:
                continue
            if isinstance(value, bool):  # int(True) == 1 would clamp to the floor
                repaired[key] = None
                continue
            try:
                number = int(value)
            except (TypeError, ValueError):
                repaired[key] = None
                continue
            repaired[key] = max(floor, number)
        return repaired


class WindowState(BaseModel):
    """Persisted per-window UI state (geometry + overlay flags)."""

    geometry: tuple[int, int, int, int] | None = None
    always_on_top: bool = True
    clickthrough: bool = False
    frameless: bool = True
    opacity: float = Field(default=1.0, ge=0.0, le=1.0)
    shown: bool = False
    # Migrated + persisted, but only the legacy ParserWindow path (helpers/
    # parser.py hover-reveal menu bar) consumes it — the new overlays have no
    # such menu bar. Kept write-only for the new windows, and reserved until
    # the legacy maps/discord windows are rebuilt on the new stack (see #8).
    auto_hide_menu: bool = True
    # Event-overlay per-region placement (keys: "lanes", "alert", "bars").
    # None = the legacy stacked QVBoxLayout (regions not independently placed).
    overlay_regions: dict[str, OverlayRegion] | None = None


class WindowLayoutPreset(BaseModel):
    """A named snapshot of window positions and sizes.

    Visibility and overlay behavior deliberately remain live settings: a
    layout only answers where each window belongs and how large it is.
    """

    geometries: dict[str, tuple[int, int, int, int]] = Field(default_factory=dict)


class GeneralSettings(BaseModel):
    eq_log_dir: Path = Field(default_factory=_default_eq_log_dir)
    eq_install_dir: Path | None = None
    update_check: bool = True
    font_size: int = Field(default=12, ge=6)
    # Overlay skin (ui/skins.py): the frame, type hierarchy and bar geometry
    # of every in-fight overlay AND the on-game event text. Applies live —
    # the tray's UI Skin submenu switches it mid-fight.
    skin: Literal["duxa", "velious", "ledger"] = "duxa"
    # Alpha of the skin's frame plate and glass, as a percent. Text, bars and
    # icons are never dimmed by it — that is the whole point of splitting it
    # off the window opacity (which fades everything).
    frame_opacity: int = Field(default=100, ge=20, le=100)
    # Height of the big on-game alert word, in px.
    overlay_text_size: int = Field(default=32, ge=14, le=72)
    # How hard the on-game alert pushes: plain, a slow opacity pulse, or the
    # pulse plus a colored glow behind the text and the newest timer bar.
    alert_emphasis: Literal["plain", "pulse", "glow"] = "pulse"
    global_audio_volume: int = Field(default=100, ge=0, le=100)
    tts_voice: str | None = None
    log_archive_enabled: bool = False
    log_archive_size_mb: int = Field(default=100, ge=1)
    # Fold macros made in game into the Macro Editor's local mirror when the
    # EQ client exits. Read-only: it never writes into the EQ directory.
    socials_autosync: bool = False
    # How long overlay alert text (ENRAGED, FTE, CH warnings...) stays on screen.
    overlay_text_seconds: float = Field(default=4.0, ge=1.0, le=30.0)
    # Soft drop-shadow behind overlay alert text. The blur effect re-renders
    # on every repaint of the translucent always-on-top overlay — measurably
    # expensive on macOS compositing; turn off if the overlay stutters.
    overlay_text_shadow: bool = True
    # How long a CH chain lane stays visible after the last CH call for its
    # target (chips in flight always keep the lane alive regardless).
    ch_lane_retention_seconds: float = Field(default=20.0, ge=5.0, le=300.0)
    # Follow only CH calls prefixed with this raid tag (e.g. "GG"); blank =
    # all calls (EQTool ChChainTagOverlay).
    ch_chain_tag: str = ""
    # nparseplus extension (#15): when the raid leader calls a cadence
    # ("healers to 4 seconds"), draw a muted marker in the CH lane at the
    # declared second. Off by default; opt-in.
    ch_cadence_indicator: bool = False
    # User-editable regexes that recognize a cadence callout — each with a
    # first capturing group for the seconds (like a trigger's search text).
    # Defaults to the stock phrasings; empty falls back to the same defaults.
    ch_cadence_patterns: list[str] = Field(
        default_factory=lambda: list(DEFAULT_CH_CADENCE_PATTERNS)
    )
    # Bard AoE hit counter: yellow overlay + TTS tally of hits/resists when a
    # bard swarm session finalizes (EQTool BardCountHandler).
    bard_count_enabled: bool = True
    # Root break warning (#79, EQTool RootHasWornOffHandler): red overlay +
    # TTS when one of your roots wears off. EQTool stores these per character
    # on PlayerInfo; the comparable alert toggles here are global, so these
    # are too (see core/handlers/root_break.py).
    root_break_overlay: bool = True
    root_break_audio: bool = True


class SharingSettings(BaseModel):
    mode: Literal["pigparse", "nparse", "off"] = "pigparse"
    pigparse_hub_url: str = "https://www.pigparse.org/PP"
    pigparse_api_url: str = "https://pigparse.azurewebsites.net"
    nparse_ws_url: str = "ws://sheeplauncher.net:8424"
    nparse_group_key: str = "public"
    player_name_override: str | None = None


class MapSettings(BaseModel):
    """Map rendering options carried by migration but not read at runtime.

    **Migration-preserved; nothing reads these (#71).** The live maps window
    is still legacy ``parsers/maps``, which reads ``config.data["maps"]`` from
    ``nparse.config.json`` — and that is also where Settings > Maps writes, so
    these fields validate and round-trip through settings.json without ever
    reaching a renderer. They are ballast held for the maps rebuild on the new
    stack, which is why they are kept rather than deleted: dropping them loses
    the migrated values silently on the next save (same call as
    ``DiscordSettings``). ``tests/config/test_unread_settings.py`` pins the
    claim in both directions.
    """

    line_width: int = Field(default=1, ge=1, le=10)
    grid_line_width: int = Field(default=1, ge=1, le=10)
    show_poi: bool = True
    show_grid: bool = True
    show_mouse_location: bool = True
    use_z_layers: bool = False
    closest_z_alpha: int = Field(default=20, ge=1, le=100)
    current_z_alpha: int = Field(default=100, ge=1, le=100)
    other_z_alpha: int = Field(default=10, ge=1, le=100)
    last_zone: str = ""
    scale: float = 0.07
    auto_follow: bool = True


class SpellWindowSettings(BaseModel):
    """Spell timer window options (ported from the legacy 'spells' section).

    The block below is the legacy half; everything from "New (EQTool parity)"
    down is live and read by the window, the handlers, or both.
    """

    # Migration-preserved; not read at runtime (#71). These came across from
    # legacy nparse, whose spell window is gone — the new one has no casting
    # window, no per-window sound file and takes the character level from
    # ActivePlayer. Kept so a migrated settings.json is not rewritten lossily
    # on the next save; unlike MapSettings, no rebuild is waiting for them.
    # Pinned by tests/config/test_unread_settings.py.
    casting_window_buffer: int = Field(default=1000, ge=1, le=4000)
    delay_self_buffs_on_zone: bool = True
    level: int = Field(default=1, ge=1, le=65)
    sound_enabled: bool = True
    sound_file: str = ""
    use_casting_window: bool = True
    use_item_triggers: bool = False
    use_custom_triggers: bool = True
    use_secondary: list[str] = Field(default_factory=lambda: ["levitate"])
    use_secondary_all: bool = False
    # New (EQTool parity) options:
    you_only_spells: bool = False
    show_random_rolls: bool = True
    # nparseplus extension (#17): EQTool's adaptive raid regrouping, redesigned.
    # When on AND a target group's distinct targets outnumber its distinct
    # spells, that group flips to spell-as-header (targets become the rows) so
    # a raid-wide buff reads as one spell over many people. Strictly opt-in;
    # targets stay the headers by default. Orientation is derived per group
    # per render (never persisted), which is what fixes the old global-flag
    # desync (stuck headers on post-/who target recognition; see
    # core/timers.py). The old ``raid_mode_auto`` key is ignored on load.
    raid_group_by_spell: bool = False
    # nparseplus extension: how rows sort under each group header. Default
    # "time_remaining" puts the soonest-to-expire row at the top (counters,
    # which never expire, sort last); "alphabetical" is the legacy order.
    row_sort: Literal["time_remaining", "alphabetical"] = "time_remaining"
    # nparseplus extension: per-category display toggles for the built-in
    # timer sections (display-only — the timers keep running and expiry
    # audio still fires while hidden).
    show_boats: bool = True
    # show_mob_timers: mob respawn ("--Dead--"), Sirran, FTE-rule countdowns.
    # show_roll_timers: Ring 8 / Scout Charisa server roll windows.
    # show_custom_timers: trigger, chat-command, and shared remote timers
    # (the merged "Custom Timers" heading — replaces the old
    # show_trigger_timers key, which is now ignored on load).
    show_mob_timers: bool = True
    show_roll_timers: bool = True
    show_custom_timers: bool = True
    # nparseplus extension: progress bars drift from their type color toward
    # red as the timer drains, so an about-to-drop buff reads as urgent
    # without reading the digits. NOT an EQTool port — EQTool's
    # ProgressBarColor is a static brush per row type — a deliberate
    # divergence. Boat rows and both kinds of roll row are excluded: their
    # remaining/total ratio is not a progress value (see ui/spellwindow._fades).
    bar_fade_to_red: bool = True
    # nparseplus extension (EQTool's best-guess is always on): when False,
    # ambiguous cast lines (multiple candidate spells) create no timer.
    best_guess_spells: bool = True
    # nparseplus extension (eqtool #239): speak when a respawn timer expires.
    respawn_expiry_audio: bool = False
    # nparseplus extension (GINA parity): warn when a self-buff is about to
    # fade. 0 disables; the time label also turns red inside the window.
    buff_fade_warning_seconds: int = Field(default=0, ge=0, le=300)
    buff_fade_warning_audio: bool = True
    # nparseplus extension (#16): post-expiration spell alerts. When enabled,
    # a spell whose name is in ``post_expiry_flash_spells`` keeps its row for
    # ``post_expiry_flash_seconds`` after it expires, flashing as a rebuff/
    # recast prompt (click the row to dismiss). Opt-in and per-spell.
    post_expiry_flash_enabled: bool = False
    post_expiry_flash_seconds: int = Field(default=30, ge=1, le=300)
    post_expiry_flash_spells: list[str] = Field(default_factory=list)


class DpsSettings(BaseModel):
    """The DPS meter's counting rules (``core.dps.FightTracker``).

    Every field here is a knob the tracker reads live — the settings page
    calls ``FightTracker.configure()`` on Apply, so none of these need a
    restart.
    """

    # What a row may count. Replaces the old ``melee_only`` bool (see the
    # migration below); the modes are documented on core.dps.DAMAGE_SOURCES.
    #   melee       - weapon and fist damage only.
    #   melee+mine  - melee plus the non-melee damage that lands inside the
    #                 credit window of one of your own casts (the default).
    #   all         - melee plus every non-melee line, the unattributable
    #                 ones parked on a "(spell damage)" pseudo-attacker
    #                 rather than credited to you.
    damage_sources: Literal["melee", "melee+mine", "all"] = "melee+mine"
    # Legacy: the pre-2.5 bool, folded into damage_sources and cleared by the
    # validator below. None means "already migrated / never written".
    melee_only: bool | None = None
    # How long after one of your casts a "was hit by non-melee" line still
    # counts as yours. Wider catches slow spells whose landing message the
    # app did not recognise; narrower is stricter about other players' nukes.
    spell_credit_window_seconds: float = Field(default=2.0, ge=0.0, le=30.0)
    # Fold your pet's damage into the session Best/Now/Last footer. Off by
    # default: the pet is an independent row, and how to count one is a
    # difference of opinion rather than a bug to fix on the user's behalf.
    # The pet keeps its own row and its "(pet)" marking either way; this only
    # decides whether your headline number is you or you+pet.
    count_pet_damage: bool = False
    # Seconds a target's group stays on screen after the last hit against it
    # from ANY attacker. 0 means never retire (zone/camp/clear only).
    # Attackers are never dropped individually — see core.dps.
    fight_retention_seconds: float = Field(default=40.0, ge=0.0, le=3600.0)
    # The trailing window the per-row "dps" number is averaged over. Always
    # divided by the full window, so short fights read low; shrink it for a
    # more responsive number, widen it for a steadier one.
    trailing_window_seconds: float = Field(default=12.0, gt=0.0, le=300.0)
    # A fight must run longer than this before your row feeds the session
    # Best/Now/Last footer (EQTool's TotalSeconds > 20). Most trash dies
    # faster, which is why that footer often sits at zero.
    session_min_fight_seconds: float = Field(default=20.0, ge=0.0, le=600.0)

    @model_validator(mode="after")
    def _fold_in_legacy_melee_only(self) -> DpsSettings:
        """Migrate ``melee_only`` -> ``damage_sources`` (the registry_url pattern).

        The mapping is LITERAL — ``true`` becomes ``melee``, ``false``
        becomes ``all`` — so nobody's meter starts counting differently
        because they upgraded. That is a deliberate split from the default
        above: a fresh install gets ``melee+mine`` because it is the mode
        that actually works for a caster, while an existing document keeps
        the behavior it already had and the user opts in when they want it.

        The temptation was to map ``true`` onto ``melee+mine`` on the grounds
        that it carries the old INTENT (``melee_only`` existed only because
        non-melee could not be attributed, which ``core.dps._attribute`` now
        fixes) rather than the old mechanism. Rejected: what a headline
        number MEANS should not change under a user without them asking,
        even in the direction of being more correct. Casters get a release
        note instead.

        Only applies when ``damage_sources`` was not written explicitly, so a
        document already saved by this version wins. Never raises:
        ``load_settings`` reads a ValueError as a corrupt document and falls
        back to defaults, which would discard everything else configured.
        """
        if self.melee_only is not None:
            if "damage_sources" not in self.model_fields_set:
                self.damage_sources = "melee" if self.melee_only else "all"
            self.melee_only = None
        return self


class DumpsSettings(BaseModel):
    """Character dump library (``/outputfile`` inventory + spellbook).

    Unlike the pigparse inventory uploader — which is off until you opt in,
    because it sends your character to a website — this only reads files the
    game already wrote and copies them into nParse+'s own data directory, so
    it defaults on. It still does nothing at all until ``eq_install_dir`` is
    set, since that is the only place dumps live.
    """

    # Pick up dumps for a character+kind the library has not seen before.
    # Also the master switch: off means no scanning happens at all.
    auto_import: bool = True
    # Store a new snapshot when a dump the library already tracks changes.
    # Off keeps the first import of each character+kind and ignores later
    # /outputfile runs — deliberate snapshots stay put.
    auto_update: bool = True
    # How many snapshots to keep per character per kind; older ones are
    # pruned as new ones land.
    keep_per_character: int = Field(default=10, ge=1, le=100)
    # Where a fresh dump gets sent, if anywhere. Deliberately one choice
    # rather than a checkbox each: both destinations publish the same
    # character to a different website, and "off" has to be the obvious
    # default. Migrated from the old pigparse_account.inventory_upload bool.
    #   pigparse   - pigparse.org character browser (needs a Discord login).
    #                Inventory only; it has no spellbook endpoint.
    #   p99planner - p99planner.com, which needs no credentials at all: it
    #                stages the export and hands back a claim link the player
    #                approves in their own browser. Takes both dump kinds.
    # The kinds each one accepts live in handlers.inventory_upload.UPLOAD_KINDS.
    upload_target: Literal["off", "pigparse", "p99planner"] = "off"


class MobInfoSettings(BaseModel):
    """What the Mob Info window looks up for the mob you considered (#113).

    Both default on: the lookup is one request per mob to a public wiki page
    the window already links to, and the picture is the reason the window is
    worth opening. They are separate toggles because they cost different
    things — ``wiki_details`` is whether nParse+ contacts
    wiki.project1999.com at all, ``show_image`` is bandwidth and screen
    space on top of that.
    """

    wiki_details: bool = True
    show_image: bool = True


class DiscordSettings(BaseModel):
    """Discord relay config carried by migration but not yet read at runtime.

    The live discord overlay is still the legacy ParserWindow, which reads its
    url from the legacy ``nparse.config.json`` (not from here). These fields are
    migration-preserved placeholders reserved for the discord-window rebuild on
    the new stack; ``channel`` has no legacy source today either (see #9).
    """

    url: str = ""
    channel: str = ""


class PigParseAccountSettings(BaseModel):
    """pigparse.org Discord-login credentials (EQTool DiscordId/ApiToken).

    ``api_token`` is a bearer credential for the inventory/auction APIs —
    treat it like a password (never log it)."""

    username: str = ""
    discord_id: str = ""
    api_token: str = ""
    # DEPRECATED and migration-only: the old single-provider inventory upload
    # gate. A Settings validator folds a True into `dumps.upload_target` and
    # clears this, so it is False in every document this version writes — the
    # field exists purely to read one written before the destination picker.
    inventory_upload: bool = False


class YouSpell(BaseModel):
    name: str
    seconds_left: int


class SpawnMarker(BaseModel):
    """A user-placed spawn-point timer on the map (nparse #10 / eqtool #190).

    ``ends_at`` is the running countdown's absolute naive-local end; None (or
    a past time) restores in the idle/popped state.
    """

    x: float
    y: float
    z: float
    length_s: int = 10
    ends_at: datetime | None = None


class WaypointMarker(BaseModel):
    """A user-placed map waypoint (the single navigation WayPoint, or a named
    user waypoint such as a corpse marker)."""

    x: float
    y: float
    z: float
    icon: str = "waypoint"
    name: str = ""


class ZoneMarkers(BaseModel):
    """Per-zone persisted map markers, keyed by the map-file short zone key."""

    spawn_points: list[SpawnMarker] = Field(default_factory=list)
    way_point: WaypointMarker | None = None
    user_waypoints: list[WaypointMarker] = Field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not (self.spawn_points or self.way_point or self.user_waypoints)


class MapMarkerStore:
    """Load/save gate the map canvas uses (the legacy maps code must not grow
    its own settings-writing conventions — this is the only bridge)."""

    def __init__(self, settings: Settings, request_save: Callable[[], None] | None = None):
        self._settings = settings
        self._request_save = request_save

    def load(self, zone_key: str) -> ZoneMarkers:
        return self._settings.map_markers.get(zone_key) or ZoneMarkers()

    def save(self, zone_key: str, markers: ZoneMarkers) -> None:
        if markers.empty:
            self._settings.map_markers.pop(zone_key, None)
        else:
            self._settings.map_markers[zone_key] = markers
        if self._request_save is not None:
            self._request_save()


class SavedTimer(BaseModel):
    """A persisted respawn/custom timer row (nparse #57).

    Unlike YouSpell's seconds-left (buff clocks freeze while camped), respawns
    keep counting in real time, so the absolute end is stored. Naive local
    datetime — the whole pipeline compares naive.

    A variable respawn ("pop") window (#125) adds two more absolute times, for
    the same reason: the window runs in the real world too. For such a row
    ``ends_at`` is when the window *opens* and ``window_ends_at`` is the latest
    possible pop, so the row is only dropped once the window has closed;
    ``window_opened_at`` is restored as saved, never re-stamped, or every
    character swap would re-announce an opening that already happened. Both are
    optional, so an existing settings.json loads unmigrated. SavedCooldown
    inherits them and never sets them — no self cooldown has a pop window.
    """

    name: str
    ends_at: datetime
    total_duration_s: float
    window_ends_at: datetime | None = None
    window_opened_at: datetime | None = None
    # Candidate-window identity (#125): which of a spawn's several possible
    # windows this row is. Empty/0 for an ordinary timer.
    window_series: str = ""
    window_index: int = 0
    window_count: int = 0


class SavedCooldown(SavedTimer):
    """A persisted YOU_GROUP reuse timer (#120).

    Same absolute-end storage as SavedTimer, and for the same reason: a reuse
    timer (LoH, Harm Touch, mend, a discipline, a spell recast, memorize) runs
    in the real world whether or not you are logged in, so camping deducts
    real elapsed time and a cooldown that came up while away is simply gone.

    ``spell_name`` names the spell a recast row belongs to so it is rebuilt as
    the SpellRow it was; empty for the cooldowns that have no spell.
    """

    spell_name: str = ""


class SavedCounter(BaseModel):
    """A persisted YOU_GROUP tally (bard song counts) (#120).

    A counter has no end time; what runs in the real world is its idle
    expiry, so the last-updated stamp is stored and a counter whose idle
    window elapsed while away is dropped on restore. Naive local datetime,
    like every other stamp in the pipeline.
    """

    name: str
    count: int
    updated_at: datetime


class PlayerInfo(BaseModel):
    name: str
    server: str
    zone: str = ""
    guild_name: str = ""
    player_class: int | None = None
    level: int | None = None
    map_location_sharing: Literal["everyone", "guild", "off"] = "everyone"
    share_timers: bool = True
    # EQTool PlayerInfo.TimerRecastSetting: recasting a detrimental spell on an
    # NPC either refreshes the running row or stacks a new one per cast.
    timer_recast: Literal["RestartCurrentTimer", "StartNewTimer"] = "RestartCurrentTimer"
    tracking_skill: int = 0
    # Spell-filter classes (PlayerClass wire ints). None = show all classes'
    # spells (EQTool's ShowSpellsForClasses null default).
    show_spells_for_classes: list[int] | None = None
    you_spells: list[YouSpell] = Field(default_factory=list)
    respawn_timers: list[SavedTimer] = Field(default_factory=list)
    # The rest of this character's own rows (#120): YOU_GROUP reuse timers and
    # tallies, hidden while camped like the buffs above but counting in real
    # time, so they are stored as absolute ends rather than seconds-left.
    you_cooldowns: list[SavedCooldown] = Field(default_factory=list)
    you_counters: list[SavedCounter] = Field(default_factory=list)


class PluginEntry(BaseModel):
    """Per-plugin consent + enablement, keyed by the plugin's meta.id.

    ``approved`` records that the first-load consent dialog was answered
    (either way) so the user is never re-asked; ``enabled`` gates activation.
    """

    enabled: bool = True
    approved: bool = False
    last_version: str = ""
    # Install provenance (registry/URL installs): where the artifact came
    # from and the sha256 of its bytes. Empty for sideloaded plugins.
    source_url: str = ""
    sha256: str = ""
    # Which registry vouched for this artifact, by index URL. "" for file/URL
    # installs and sideloads. Recorded rather than resolved to a name so that
    # removing a registry later doesn't falsify the record.
    registry_url: str = ""
    # Mirror of the plugin's own PluginMeta.update_url, so the update checker
    # can still reach the feed of a plugin that is installed but not loaded
    # (consent declined, disabled, incompatible). The live meta wins whenever
    # there is one; this is the cache for when there isn't.
    update_url: str = ""


def normalize_registry_url(url: str) -> str:
    """Canonical form of a registry index URL; raises ValueError if unusable.

    Lives here rather than in ``core.plugins.registry`` because settings is
    where these URLs are stored and compared, and because ``PluginsSettings``
    must be able to sanitize them without importing the plugin subsystem —
    every user constructs a ``Settings``, including the ones who never turn
    add-ons on.

    Scheme and host are lower-cased so two spellings of the same registry
    can't both be added; the path is left alone (it is case-sensitive).
    """
    cleaned = url.strip()
    if not cleaned:
        raise ValueError("registry url must not be empty")
    scheme, separator, rest = cleaned.partition("://")
    if not separator or scheme.lower() != "https":
        raise ValueError("registry url must be https://")
    host, slash, path = rest.partition("/")
    if not host:
        raise ValueError("registry url has no host")
    return f"https://{host.lower()}{slash}{path}"


class RegistrySource(BaseModel):
    """A user-added plugin registry.

    The built-in default is deliberately NOT stored here — see
    ``core.plugins.registry.resolve_registries``. Identity is the URL: `name`
    is cosmetic, and re-pointing an entry at a different origin is a new
    trust decision that should go through the add flow, not a rename.
    """

    url: str
    name: str = ""  # "" -> the UI shows the host
    enabled: bool = True


class PluginsSettings(BaseModel):
    # Master switch for the whole add-on subsystem, off until asked for.
    # Plugins are third-party code running with the app's full permissions,
    # and most users only want maps and spell timers: while this is False
    # nothing plugin-related is discovered, imported, or shown anywhere.
    enabled: bool = False
    entries: dict[str, PluginEntry] = Field(default_factory=dict)
    # DEPRECATED: the single-registry override, folded into `registries` by
    # the validator below and cleared. Kept for one release so a downgrade
    # then upgrade doesn't lose a hand-set override.
    registry_url: str = ""
    # User-added registries only. The built-in default is synthesized at read
    # time from DEFAULT_REGISTRY_URL and only its enabled bit persists, so
    # changing that constant moves every user instead of stranding them on a
    # URL a past release happened to write into their settings.json.
    registries: list[RegistrySource] = Field(default_factory=list)
    default_registry_enabled: bool = True
    # Poll the enabled registries (and each plugin's declared update feed)
    # shortly after launch so Settings > Plugins can say what is out of date
    # without the user opening Browse first. Only meaningful while `enabled`
    # is True — with add-ons off, nothing plugin-shaped is even imported.
    # Matches general.update_check, which defaults on for the app itself.
    update_check: bool = True

    @model_validator(mode="after")
    def _fold_in_legacy_and_sanitize(self) -> PluginsSettings:
        """Migrate `registry_url` and drop unusable registry entries.

        Deliberately never raises: ``load_settings`` treats a ValueError as a
        corrupt document and falls back to defaults, so raising here would
        throw away everything the user has ever configured over one bad
        registry line. Drop the line instead.
        """
        seen: set[str] = set()
        cleaned: list[RegistrySource] = []
        for source in self.registries:
            try:
                url = normalize_registry_url(source.url)
            except ValueError:
                continue
            if url in seen:
                continue
            seen.add(url)
            cleaned.append(source.model_copy(update={"url": url}))

        if self.registry_url:
            try:
                legacy = normalize_registry_url(self.registry_url)
            except ValueError:
                legacy = ""
            if legacy and legacy not in seen:
                cleaned.append(RegistrySource(url=legacy))
            self.registry_url = ""

        self.registries = cleaned
        return self


class Settings(BaseModel):
    """Root settings document persisted to settings.json."""

    schema_version: int = SCHEMA_VERSION
    general: GeneralSettings = Field(default_factory=GeneralSettings)
    sharing: SharingSettings = Field(default_factory=SharingSettings)
    maps: MapSettings = Field(default_factory=MapSettings)
    spellwindow: SpellWindowSettings = Field(default_factory=SpellWindowSettings)
    discord: DiscordSettings = Field(default_factory=DiscordSettings)
    dps: DpsSettings = Field(default_factory=DpsSettings)
    dumps: DumpsSettings = Field(default_factory=DumpsSettings)
    mobinfo: MobInfoSettings = Field(default_factory=MobInfoSettings)
    pigparse_account: PigParseAccountSettings = Field(default_factory=PigParseAccountSettings)
    plugins: PluginsSettings = Field(default_factory=PluginsSettings)
    windows: dict[str, WindowState] = Field(default_factory=dict)
    window_layouts: dict[str, WindowLayoutPreset] = Field(default_factory=dict)
    # Persisted map markers per zone short key (nparse #10 / eqtool #190).
    # Deliberately in the NEW settings, not the legacy maps config: durable
    # user data that must outlive the planned maps-window rebuild.
    map_markers: dict[str, ZoneMarkers] = Field(default_factory=dict)
    players: list[PlayerInfo] = Field(default_factory=list)
    triggers: list[Trigger] = Field(default_factory=list)
    # Raw legacy custom timers ([name, matchtext, "hh:mm:ss"]) kept verbatim so a
    # legacy import is lossless even after conversion to Trigger entries.
    custom_timers: list[list[str]] = Field(default_factory=list)

    @model_validator(mode="after")
    def _fold_in_legacy_upload_toggle(self) -> Settings:
        """Migrate ``pigparse_account.inventory_upload`` -> ``dumps.upload_target``.

        Only when the new field is still at its default: a document that has
        already been written by this version wins, so a stale legacy bool
        cannot resurrect a provider the user has since switched away from.

        Like ``PluginsSettings``, this never raises — ``load_settings`` reads
        a ValueError as a corrupt document and falls back to defaults, which
        would throw away everything else the user has configured.
        """
        if self.pigparse_account.inventory_upload:
            if self.dumps.upload_target == "off":
                self.dumps.upload_target = "pigparse"
            self.pigparse_account.inventory_upload = False
        return self


def plugins_enabled(settings: Settings, environ: Mapping[str, str] | None = None) -> bool:
    """Whether the add-on subsystem should run at all.

    The env var is a veto, never an enabler: ``NPARSEPLUS_NO_PLUGINS=1`` is
    the safe-mode switch for recovering from a plugin that breaks startup, so
    it must be able to turn plugins off but must never turn them on for a user
    who never opted in.

    This is the single place the setting and the env var combine — every gate
    in the app calls it rather than re-deriving the answer.
    """
    env = os.environ if environ is None else environ
    if env.get(NO_PLUGINS_ENV_VAR) == "1":
        return False
    return settings.plugins.enabled


def load_settings(path: Path | None = None) -> Settings:
    """Load settings from ``path`` (default: the platform settings path).

    Missing file: returns defaults, first attempting a legacy
    ``nparse.config.json`` migration (CWD and beside the settings dir).
    A corrupt file falls back to defaults rather than raising.
    """
    if path is None:
        path = settings_path()
    if not path.exists():
        # Local import: migrate.py imports the models above.
        from nparseplus.config.migrate import find_legacy_config, migrate_legacy

        legacy = find_legacy_config(settings_dir=path.parent)
        if legacy is not None:
            migrated = migrate_legacy(legacy)
            if migrated is not None:
                return migrated
        return Settings()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return Settings.model_validate(raw)
    except (OSError, ValueError):
        return Settings()


def save_settings(settings: Settings, path: Path | None = None) -> None:
    """Atomically write ``settings`` as indented JSON (tmp file + rename)."""
    if path is None:
        ensure_config_dir()
        path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(settings.model_dump(mode="json"), indent=2)
    tmp_path = path.with_name(path.name + ".tmp")
    tmp_path.write_text(payload, encoding="utf-8")
    tmp_path.replace(path)


def find_player(settings: Settings, name: str, server: str) -> PlayerInfo | None:
    """Return the PlayerInfo for (name, server) if one exists (no creation)."""
    for player in settings.players:
        if player.name == name and player.server == server:
            return player
    return None


def get_player(settings: Settings, name: str, server: str) -> PlayerInfo:
    """Return the PlayerInfo for (name, server), creating and appending it if absent."""
    player = find_player(settings, name, server)
    if player is None:
        player = PlayerInfo(name=name, server=server)
        settings.players.append(player)
    return player


class DebouncedSaver:
    """Coalesces bursts of save requests into a single deferred save.

    Thread-safe. Each ``request_save()`` (re)arms a ``threading.Timer``; only
    the last request within ``delay`` seconds triggers the save callable.
    ``flush()`` runs any pending save immediately; ``cancel()`` discards it.
    """

    def __init__(self, save: Callable[[], None], delay: float = 1.0) -> None:
        self._save = save
        self._delay = delay
        self._lock = threading.Lock()
        self._timer: threading.Timer | None = None
        self._pending = False

    def request_save(self) -> None:
        with self._lock:
            self._pending = True
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(self._delay, self._on_timer)
            self._timer.daemon = True
            self._timer.start()

    def _on_timer(self) -> None:
        with self._lock:
            if not self._pending:
                return
            self._pending = False
            self._timer = None
        self._save()

    def flush(self) -> None:
        """Run any pending save now (synchronously) and disarm the timer."""
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            pending = self._pending
            self._pending = False
        if pending:
            self._save()

    def cancel(self) -> None:
        """Discard any pending save without running it."""
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            self._pending = False
