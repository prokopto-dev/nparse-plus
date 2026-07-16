"""Explicit composition root — wires the Qt-free backend together.

No DI framework (replaces EQTool's Autofac and nparse's ``_signals`` dict):
one function builds the singletons in dependency order and returns them in a
container. The UI layer (app.py) attaches on top via the Qt bridge.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from nparseplus.config.settings import Settings
from nparseplus.core.bus import EventBus
from nparseplus.core.dps import FightTracker
from nparseplus.core.driver import LogDriver
from nparseplus.core.handlers.bard_count import BardCountHandler
from nparseplus.core.handlers.boat import BoatHandler
from nparseplus.core.handlers.complete_heal import CompleteHealCommsHandler, CompleteHealHandler
from nparseplus.core.handlers.con import ConHandler, MobInfoState
from nparseplus.core.handlers.death_loop import DeathLoopHandler
from nparseplus.core.handlers.discipline_cooldown import DisciplineCooldownHandler
from nparseplus.core.handlers.dps import DpsHandler
from nparseplus.core.handlers.fte import FTEHandler
from nparseplus.core.handlers.group_leader import GroupLeaderHandler
from nparseplus.core.handlers.mend_wounds import MendWoundsHandler
from nparseplus.core.handlers.pet import PetHandler
from nparseplus.core.handlers.quake import QuakeHandler
from nparseplus.core.handlers.random_roll import RandomRollHandler
from nparseplus.core.handlers.ring_war import RingWarHandler
from nparseplus.core.handlers.spawn_timer import SpawnTimerHandler
from nparseplus.core.handlers.spell_timers import SpellTimerHandler
from nparseplus.core.handlers.you_zoned import YouZonedHandler
from nparseplus.core.logarchive import LogArchiveService
from nparseplus.core.parsers.base import ParseContext
from nparseplus.core.parsers.registry import build_parser_chain
from nparseplus.core.pets import PlayerPet, load_pets
from nparseplus.core.pipeline import LogPipeline
from nparseplus.core.player import ActivePlayer
from nparseplus.core.sharing import SharingClient, SharingCoordinator
from nparseplus.core.spells.spells_us import SpellBook, load_master_npc_list, load_spell_book
from nparseplus.core.timers import TimerRow, TimersService
from nparseplus.core.triggers.builtin import sync_builtin_triggers
from nparseplus.core.triggers.chat_commands import CustomTimerChatCommands
from nparseplus.core.triggers.engine import TriggerEngine
from nparseplus.core.zones import ZoneDatabase, load_zone_database
from nparseplus.net.pigparse_api import PigParseApiClient
from nparseplus.net.pigparse_hub import PigParseHubClient
from nparseplus.net.worker import NetWorker

TRIGGER_TIMER_GROUP = "Timers"


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
    sharing: SharingCoordinator
    sharing_client: SharingClient | None = None
    pigparse_api: PigParseApiClient | None = None
    net_worker: NetWorker | None = None
    # Handlers/subscribers kept alive for the app lifetime.
    _retained: list[object] = field(default_factory=list)

    def start(self) -> None:
        if self.net_worker is not None:
            self.net_worker.start()
        if self.sharing_client is not None:
            self.sharing_client.start()
        self.driver.start()

    def stop(self) -> None:
        self.driver.stop()
        if self.sharing_client is not None:
            self.sharing_client.stop()
        if self.net_worker is not None:
            self.net_worker.stop()


def _spells_path(settings: Settings) -> Path:
    install = settings.general.eq_install_dir
    if install:
        candidate = Path(install) / "spells_us.txt"
        if candidate.is_file():
            return candidate
    return Path("data/spells/spells_us.txt")  # bundled fallback (repo/app root)


def build_backend(settings: Settings, speaker=None) -> Backend:
    bus = EventBus()
    player = ActivePlayer()
    zones = load_zone_database()
    spells = load_spell_book(_spells_path(settings))
    timers = TimersService()

    ctx = ParseContext(bus=bus, player=player, spells=spells, zones=zones)
    pipeline = LogPipeline(build_parser_chain(), ctx)
    driver = LogDriver(Path(settings.general.eq_log_dir), pipeline, bus, player)

    if speaker is None:
        from nparseplus.audio.tts import default_speaker

        speaker = default_speaker(
            voice=settings.general.tts_voice or "",
            volume=settings.general.global_audio_volume / 100,
        )

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

    fights = FightTracker()
    mob_info = MobInfoState()
    pets = load_pets()
    player_pet = PlayerPet()
    npcs = load_master_npc_list()

    handlers: list[object] = [
        YouZonedHandler(bus, player),
        SpellTimerHandler(bus, player, spells, timers),
        DpsHandler(bus, player, fights),
        SpawnTimerHandler(bus, player, timers, zones, npcs=npcs),
        RandomRollHandler(bus, player, timers),
        FTEHandler(bus, player, timers, speaker=speaker),
        QuakeHandler(bus, player, speaker=speaker),
        RingWarHandler(bus, player, timers),
        BoatHandler(bus, player, timers, zones),
        PetHandler(bus, player, pets, player_pet=player_pet),
        ConHandler(bus, player, zones, player_pet=player_pet, mob_info=mob_info),
        DisciplineCooldownHandler(bus, player, timers),
        MendWoundsHandler(bus, player, timers),
        CompleteHealCommsHandler(bus, player, npcs=npcs),
        CompleteHealHandler(bus, player, speaker=speaker),
        BardCountHandler(bus, player, speaker=speaker, timers=timers),
        DeathLoopHandler(bus, player, speaker=speaker),
        GroupLeaderHandler(bus, player),
    ]

    archiver = LogArchiveService(
        get_log_dir=lambda: settings.general.eq_log_dir,
        is_enabled=lambda: settings.general.log_archive_enabled,
        get_threshold_mb=lambda: settings.general.log_archive_size_mb,
    )

    # Sharing: the coordinator always exists (it is the mode gate); the
    # network client and REST plumbing only when a sharing mode is on.
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
    # (the "nparse" websocket mode lands with the M3 fallback step)
    sharing.set_client(sharing_client)

    driver.on_tick.append(timers.tick)
    driver.on_tick.append(engine.tick)
    driver.on_tick.append(fights.tick)
    driver.on_tick.append(archiver.tick)
    driver.on_tick.append(sharing.tick)

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
        sharing=sharing,
        sharing_client=sharing_client,
        pigparse_api=pigparse_api,
        net_worker=net_worker,
        _retained=[chat_commands, sink, *handlers],
    )
