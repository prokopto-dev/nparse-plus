"""Explicit composition root — wires the Qt-free backend together.

No DI framework (replaces EQTool's Autofac and nparse's ``_signals`` dict):
one function builds the singletons in dependency order and returns them in a
container. The UI layer (app.py) attaches on top via the Qt bridge.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from nparseplus.config.settings import Settings, find_player
from nparseplus.core.bus import EventBus
from nparseplus.core.dps import FightTracker
from nparseplus.core.driver import LogDriver
from nparseplus.core.handlers.api_timers import ApiTimersService
from nparseplus.core.handlers.bard_count import BardCountHandler
from nparseplus.core.handlers.boat import BoatHandler
from nparseplus.core.handlers.buff_warning import BuffFadeWarner
from nparseplus.core.handlers.complete_heal import CompleteHealCommsHandler, CompleteHealHandler
from nparseplus.core.handlers.consider import ConHandler, MobInfoState
from nparseplus.core.handlers.corpse import CorpseWaypointHandler
from nparseplus.core.handlers.death_loop import DeathLoopHandler
from nparseplus.core.handlers.discipline_cooldown import DisciplineCooldownHandler
from nparseplus.core.handlers.dps import DpsHandler
from nparseplus.core.handlers.fte import FTEHandler
from nparseplus.core.handlers.group_leader import GroupLeaderHandler
from nparseplus.core.handlers.mend_wounds import MendWoundsHandler
from nparseplus.core.handlers.pet import PetHandler
from nparseplus.core.handlers.player_profile import PlayerProfileHandler
from nparseplus.core.handlers.player_tracker import PlayerTrackerHandler
from nparseplus.core.handlers.quake import QuakeHandler
from nparseplus.core.handlers.random_roll import RandomRollHandler
from nparseplus.core.handlers.respawn_expiry import RespawnExpiryNotifier
from nparseplus.core.handlers.ring_war import RingWarHandler
from nparseplus.core.handlers.spawn_timer import SpawnTimerHandler
from nparseplus.core.handlers.spell_timers import SpellTimerHandler
from nparseplus.core.handlers.timer_persistence import TimerPersistenceHandler
from nparseplus.core.handlers.you_zoned import YouZonedHandler
from nparseplus.core.handlers.zone_activity import ZoneActivityHandler
from nparseplus.core.inventory import InventoryWatcher
from nparseplus.core.logarchive import LogArchiveService
from nparseplus.core.parsers.base import ParseContext
from nparseplus.core.parsers.registry import build_parser_chain
from nparseplus.core.pets import PlayerPet, load_pets
from nparseplus.core.pipeline import LogPipeline
from nparseplus.core.player import ActivePlayer
from nparseplus.core.sharing import SharingClient, SharingCoordinator
from nparseplus.core.spells.spells_us import SpellBook, load_master_npc_list, load_spell_book
from nparseplus.core.timers import TRIGGER_TIMER_GROUP, TimerRow, TimersService
from nparseplus.core.triggers.builtin import sync_builtin_triggers
from nparseplus.core.triggers.chat_commands import CustomTimerChatCommands
from nparseplus.core.triggers.engine import TriggerEngine
from nparseplus.core.triggers.window_commands import WindowChatCommands
from nparseplus.core.zones import ZoneDatabase, load_zone_database
from nparseplus.net.nparse_ws import NParseWsClient
from nparseplus.net.pigparse_api import PigParseApiClient
from nparseplus.net.pigparse_hub import PigParseHubClient
from nparseplus.net.worker import NetWorker


class TriggerTimerSink:
    """Adapts the TriggerEngine's TimerSink protocol onto TimersService."""

    def __init__(self, timers: TimersService) -> None:
        self._timers = timers

    def add_timer(
        self, name: str, seconds: int, color: str, icon: str, restart_behavior: str
    ) -> None:
        now = datetime.now()
        row = self._timers.find(name, TRIGGER_TIMER_GROUP)
        if isinstance(row, TimerRow) and restart_behavior != "StartNewTimer":
            if restart_behavior == "RestartTimer":
                row.ends_at = now + timedelta(seconds=seconds)
                row.updated_at = now
            return
        self._timers.add_timer(
            TimerRow(
                name=name,
                group=TRIGGER_TIMER_GROUP,
                updated_at=now,
                ends_at=now + timedelta(seconds=seconds),
                total_duration_s=float(seconds),
            ),
            allow_duplicates=restart_behavior == "StartNewTimer",
        )

    def cancel(self, name: str) -> None:
        row = self._timers.find(name, TRIGGER_TIMER_GROUP)
        if row is not None:
            self._timers.remove_row(row)


class _SwappableSpeaker:
    """A Speaker whose delegate can be replaced at runtime.

    ``build_backend`` hands this one object to the trigger engine and every
    audio handler, so swapping its delegate live-changes the TTS voice for all
    of them without rebuilding the backend (or restarting the app). Implements
    the ``Speaker`` protocol (``speak``) plus ``close`` for shutdown parity
    with ``SubprocessSpeaker``.
    """

    def __init__(self, delegate: object) -> None:
        self._delegate = delegate

    def speak(self, text: str) -> None:
        self._delegate.speak(text)  # type: ignore[attr-defined]

    def close(self) -> None:
        self._close(self._delegate)

    def swap(self, delegate: object) -> None:
        old = self._delegate
        self._delegate = delegate
        if old is not delegate:
            self._close(old)  # a SubprocessSpeaker leaks its worker thread otherwise

    @staticmethod
    def _close(speaker: object) -> None:
        close = getattr(speaker, "close", None)
        if callable(close):
            close()


@dataclass
class Backend:
    """Everything the UI needs a handle on. Qt-free."""

    settings: Settings
    bus: EventBus
    player: ActivePlayer
    spells: SpellBook
    zones: ZoneDatabase
    timers: TimersService
    trigger_engine: TriggerEngine
    pipeline: LogPipeline
    driver: LogDriver
    fights: FightTracker
    mob_info: MobInfoState
    player_pet: PlayerPet
    speaker: _SwappableSpeaker
    sharing: SharingCoordinator
    sharing_client: SharingClient | None = None
    pigparse_api: PigParseApiClient | None = None
    net_worker: NetWorker | None = None
    player_tracker: PlayerTrackerHandler | None = None
    timer_persistence: TimerPersistenceHandler | None = None
    # Handlers/subscribers kept alive for the app lifetime.
    _retained: list[object] = field(default_factory=list)

    def start(self) -> None:
        if self.net_worker is not None:
            self.net_worker.start()
        if self.sharing_client is not None:
            self.sharing_client.start()
        self.driver.start()

    def set_speaker(self, speaker: object) -> None:
        """Live-swap the shared TTS voice. The trigger engine and every audio
        handler speak through one holder, so replacing its delegate updates
        them all at once; the previous speaker is closed."""
        self.speaker.swap(speaker)

    def rebuild_speaker(self) -> None:
        """Rebuild the shared speaker from the current audio settings and swap
        it in — the seam the settings window calls when TTS voice/volume change
        so running handlers stop using the old voice without a restart."""
        from nparseplus.audio.tts import default_speaker

        self.set_speaker(
            default_speaker(
                voice=self.settings.general.tts_voice or "",
                volume=self.settings.general.global_audio_volume / 100,
            )
        )

    def stop(self) -> None:
        self.driver.stop()
        if self.timer_persistence is not None:
            # After the driver thread is joined: quit-time seconds-left for the
            # you_spells store (the app's aboutToQuit save flush runs next).
            self.timer_persistence.export_now()
        if self.sharing_client is not None:
            self.sharing_client.stop()
        if self.net_worker is not None:
            self.net_worker.stop()
        if self.pigparse_api is not None:
            self.pigparse_api.close()


def _spells_path(settings: Settings) -> Path:
    install = settings.general.eq_install_dir
    if install:
        candidate = Path(install) / "spells_us.txt"
        if candidate.is_file():
            return candidate
    return Path("data/spells/spells_us.txt")  # bundled fallback (repo/app root)


def build_backend(settings: Settings, speaker=None, request_save=None) -> Backend:
    """``request_save`` is the app's DebouncedSaver.request_save (thread-safe);
    driver-thread handlers use it to persist per-character profile changes."""
    bus = EventBus()
    player = ActivePlayer()
    zones = load_zone_database()
    spells = load_spell_book(_spells_path(settings))
    timers = TimersService()

    ctx = ParseContext(bus=bus, player=player, spells=spells, zones=zones, settings=settings)
    pipeline = LogPipeline(build_parser_chain(), ctx)
    driver = LogDriver(Path(settings.general.eq_log_dir), pipeline, bus, player)

    if speaker is None:
        from nparseplus.audio.tts import default_speaker

        speaker = default_speaker(
            voice=settings.general.tts_voice or "",
            volume=settings.general.global_audio_volume / 100,
        )
    # One holder shared by the engine + every audio handler below, so a voice
    # change only has to swap this delegate (see Backend.rebuild_speaker).
    speaker = _SwappableSpeaker(speaker)

    sink = TriggerTimerSink(timers)
    engine = TriggerEngine(
        bus=bus,
        player=player,
        speaker=speaker,
        timers=sink,
        display_text_seconds=settings.general.overlay_text_seconds,
    )
    synced, changed = sync_builtin_triggers(list(settings.triggers))
    if changed:
        settings.triggers = synced
    engine.set_triggers(synced)
    chat_commands = CustomTimerChatCommands(bus, sink)
    window_commands = WindowChatCommands(bus)

    fights = FightTracker()
    mob_info = MobInfoState()
    pets = load_pets()
    player_pet = PlayerPet()
    npcs = load_master_npc_list()

    # Sharing: the coordinator always exists (it is the mode gate); the
    # network client and REST plumbing only when a sharing mode is on.
    # Handlers get api+submit (or None/None, turning their sends into
    # no-ops); results apply back on the driver thread via the coordinator
    # inbox.
    sharing = SharingCoordinator(
        bus=bus,
        player=player,
        settings=settings,
        timers=timers,
        last_you_activity=lambda: pipeline.last_you_activity,
    )
    sharing_client: SharingClient | None = None
    pigparse_api: PigParseApiClient | None = None
    net_worker: NetWorker | None = None
    if settings.sharing.mode == "pigparse":
        sharing_client = PigParseHubClient(
            url=settings.sharing.pigparse_hub_url,
            on_inbound=sharing.enqueue_inbound,
        )
        pigparse_api = PigParseApiClient(settings.sharing.pigparse_api_url)
        net_worker = NetWorker(deliver=sharing.enqueue_inbound)
    elif settings.sharing.mode == "nparse":
        sharing_client = NParseWsClient(
            url=settings.sharing.nparse_ws_url,
            group_key=settings.sharing.nparse_group_key,
            on_inbound=sharing.enqueue_inbound,
            zones=zones,
        )
    sharing.set_client(sharing_client)
    submit = net_worker.submit if net_worker is not None else None

    def timer_recast() -> str:
        """The active character's PlayerInfo.TimerRecastSetting."""
        server_key = player.server_key
        info = find_player(settings, player.name, server_key) if server_key else None
        return info.timer_recast if info is not None else "RestartCurrentTimer"

    player_tracker = PlayerTrackerHandler(bus, player, api=pigparse_api, submit=submit)
    profile_handler = PlayerProfileHandler(bus, player, settings, request_save=request_save)
    # Constructed (= subscribed) after PlayerProfileHandler: restore-on-player-
    # change needs the profile's class/level already loaded into ActivePlayer.
    timer_persistence = TimerPersistenceHandler(
        bus, player, settings, timers, spells, request_save=request_save
    )
    handlers: list[object] = [
        YouZonedHandler(bus, player),
        profile_handler,
        timer_persistence,
        player_tracker,
        SpellTimerHandler(
            bus,
            player,
            spells,
            timers,
            spell_settings=settings.spellwindow,
            timer_recast=timer_recast,
        ),
        DpsHandler(bus, player, fights),
        SpawnTimerHandler(bus, player, timers, zones, npcs=npcs, timer_recast=timer_recast),
        RespawnExpiryNotifier(timers, speaker, settings.spellwindow),
        CorpseWaypointHandler(bus, player),
        RandomRollHandler(bus, player, timers),
        FTEHandler(bus, player, timers, speaker=speaker, api=pigparse_api, submit=submit),
        QuakeHandler(bus, player, speaker=speaker, api=pigparse_api, submit=submit),
        RingWarHandler(bus, player, timers),
        BoatHandler(bus, player, timers, zones, api=pigparse_api, submit=submit),
        PetHandler(bus, player, pets, player_pet=player_pet),
        ConHandler(
            bus,
            player,
            zones,
            player_pet=player_pet,
            mob_info=mob_info,
            api=pigparse_api,
            submit=submit,
        ),
        ZoneActivityHandler(bus, player, zones, api=pigparse_api, submit=submit),
        DisciplineCooldownHandler(bus, player, timers),
        MendWoundsHandler(bus, player, timers),
        CompleteHealCommsHandler(
            bus, player, npcs=npcs, ch_chain_tag=lambda: settings.general.ch_chain_tag
        ),
        CompleteHealHandler(bus, player, speaker=speaker),
        BardCountHandler(
            bus,
            player,
            speaker=speaker,
            timers=timers,
            enabled=lambda: settings.general.bard_count_enabled,
        ),
        DeathLoopHandler(bus, player, speaker=speaker),
        GroupLeaderHandler(bus, player),
    ]
    api_timers = ApiTimersService(timers, zones, player, api=pigparse_api, submit=submit)
    inventory_watcher = InventoryWatcher(
        player,
        get_eq_dir=lambda: (
            Path(settings.general.eq_install_dir) if settings.general.eq_install_dir else None
        ),
        is_enabled=lambda: settings.pigparse_account.inventory_upload,
        get_token=lambda: settings.pigparse_account.api_token,
        api=pigparse_api,
        submit=submit,
    )

    archiver = LogArchiveService(
        get_log_dir=lambda: settings.general.eq_log_dir,
        is_enabled=lambda: settings.general.log_archive_enabled,
        get_threshold_mb=lambda: settings.general.log_archive_size_mb,
    )

    buff_warner = BuffFadeWarner(bus, timers, speaker, settings.spellwindow)
    driver.on_tick.append(timers.tick)
    driver.on_tick.append(buff_warner.tick)
    driver.on_tick.append(engine.tick)
    driver.on_tick.append(fights.tick)
    driver.on_tick.append(archiver.tick)
    driver.on_tick.append(sharing.tick)
    driver.on_tick.append(api_timers.tick)
    driver.on_tick.append(player_tracker.tick)
    driver.on_tick.append(inventory_watcher.tick)

    return Backend(
        settings=settings,
        bus=bus,
        player=player,
        spells=spells,
        zones=zones,
        timers=timers,
        trigger_engine=engine,
        pipeline=pipeline,
        driver=driver,
        fights=fights,
        mob_info=mob_info,
        player_pet=player_pet,
        speaker=speaker,
        sharing=sharing,
        sharing_client=sharing_client,
        pigparse_api=pigparse_api,
        net_worker=net_worker,
        player_tracker=player_tracker,
        timer_persistence=timer_persistence,
        _retained=[chat_commands, window_commands, sink, *handlers],
    )
