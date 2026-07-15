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
from nparseplus.core.driver import LogDriver
from nparseplus.core.handlers.spell_timers import SpellTimerHandler
from nparseplus.core.parsers.base import ParseContext
from nparseplus.core.parsers.registry import build_parser_chain
from nparseplus.core.pipeline import LogPipeline
from nparseplus.core.player import ActivePlayer
from nparseplus.core.spells.spells_us import SpellBook, load_spell_book
from nparseplus.core.timers import TimerRow, TimersService
from nparseplus.core.triggers.builtin import sync_builtin_triggers
from nparseplus.core.triggers.chat_commands import CustomTimerChatCommands
from nparseplus.core.triggers.engine import TriggerEngine
from nparseplus.core.zones import ZoneDatabase, load_zone_database

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
    # Handlers/subscribers kept alive for the app lifetime.
    _retained: list[object] = field(default_factory=list)

    def start(self) -> None:
        self.driver.start()

    def stop(self) -> None:
        self.driver.stop()


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
    engine = TriggerEngine(bus=bus, player=player, speaker=speaker, timers=sink)
    synced, changed = sync_builtin_triggers(list(settings.triggers))
    if changed:
        settings.triggers = synced
    engine.set_triggers(synced)
    chat_commands = CustomTimerChatCommands(bus, sink)
    spell_handler = SpellTimerHandler(bus, player, spells, timers)

    driver.on_tick.append(timers.tick)
    driver.on_tick.append(engine.tick)

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
        _retained=[chat_commands, spell_handler, sink],
    )
