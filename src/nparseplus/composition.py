"""Explicit composition root — wires the Qt-free backend together.

No DI framework (replaces EQTool's Autofac and nparse's ``_signals`` dict):
one function builds the singletons in dependency order and returns them in a
container. The UI layer (app.py) attaches on top via the Qt bridge.
"""

from __future__ import annotations

import functools
import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from nparseplus.config.paths import (
    ensure_dumps_dir,
    ensure_socials_dir,
    ensure_wiki_image_cache_dir,
)
from nparseplus.config.settings import Settings, find_player
from nparseplus.core.background import BackgroundJob, Spawn
from nparseplus.core.bus import EventBus
from nparseplus.core.dps import FightTracker
from nparseplus.core.driver import LogDriver
from nparseplus.core.dumps import DumpLibrary, DumpWatcher
from nparseplus.core.handlers.ability_cooldowns import AbilityCooldownHandler
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
from nparseplus.core.handlers.dps_persistence import DpsPersistenceHandler
from nparseplus.core.handlers.fte import FTEHandler
from nparseplus.core.handlers.group_leader import GroupLeaderHandler
from nparseplus.core.handlers.inventory_upload import InventoryUploadHandler
from nparseplus.core.handlers.mend_wounds import MendWoundsHandler
from nparseplus.core.handlers.pet import PetHandler
from nparseplus.core.handlers.player_profile import PlayerProfileHandler
from nparseplus.core.handlers.player_tracker import PlayerTrackerHandler
from nparseplus.core.handlers.quake import QuakeHandler
from nparseplus.core.handlers.random_roll import RandomRollHandler
from nparseplus.core.handlers.respawn_expiry import RespawnExpiryNotifier
from nparseplus.core.handlers.ring_war import RingWarHandler
from nparseplus.core.handlers.root_break import RootBreakHandler
from nparseplus.core.handlers.spawn_timer import SpawnTimerHandler
from nparseplus.core.handlers.spell_timers import SpellTimerHandler
from nparseplus.core.handlers.timer_persistence import TimerPersistenceHandler
from nparseplus.core.handlers.timer_window import TimerWindowNotifier
from nparseplus.core.handlers.you_zoned import YouZonedHandler
from nparseplus.core.handlers.zone_activity import ZoneActivityHandler
from nparseplus.core.logarchive import LogArchiveService
from nparseplus.core.parsers.base import ParseContext
from nparseplus.core.parsers.camp import CampParser
from nparseplus.core.parsers.registry import build_parser_chain
from nparseplus.core.pets import PlayerPet, load_pets
from nparseplus.core.pipeline import LogPipeline
from nparseplus.core.player import ActivePlayer
from nparseplus.core.sharing import SharingClient, SharingCoordinator, sharing_gated_submit
from nparseplus.core.socialsync import SocialSyncWatcher
from nparseplus.core.spells.spells_us import SpellBook, load_master_npc_list, load_spell_book
from nparseplus.core.testalerts import AlertTestRunner
from nparseplus.core.timers import TRIGGER_TIMER_GROUP, TimerRow, TimersService
from nparseplus.core.triggers.builtin import sync_builtin_triggers
from nparseplus.core.triggers.chat_commands import CustomTimerChatCommands
from nparseplus.core.triggers.engine import TriggerEngine
from nparseplus.core.triggers.window_commands import WindowChatCommands
from nparseplus.core.zones import ZoneDatabase, load_zone_database
from nparseplus.net.nparse_ws import NParseWsClient
from nparseplus.net.p99planner import P99PlannerClient
from nparseplus.net.p99wiki import P99WikiClient
from nparseplus.net.pigparse_api import PigParseApiClient
from nparseplus.net.pigparse_hub import PigParseHubClient
from nparseplus.net.worker import NetWorker

logger = logging.getLogger(__name__)


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
    the ``Speaker`` protocol (``speak`` / ``interrupt``) plus ``close`` for
    shutdown parity with ``SubprocessSpeaker``.
    """

    def __init__(self, delegate: object) -> None:
        self._delegate = delegate

    def speak(self, text: str) -> None:
        self._delegate.speak(text)  # type: ignore[attr-defined]

    def interrupt(self) -> None:
        interrupt = getattr(self._delegate, "interrupt", None)
        if callable(interrupt):
            interrupt()

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


class _SpellBookReloader:
    """Swaps the spell database under the objects holding it (#70).

    The speaker above is live-swappable because everything speaks through one
    holder; the spell book cannot be, because four handlers and the parse
    context captured the object itself. So the swap is
    :meth:`SpellBook.adopt` — same object, new contents — and what is left to
    arrange is *which thread*, on both halves:

    * Parsing spells_us.txt costs **~1.1 s**. That is four times the budget a
      plugin tick is evicted for, on the thread that tails the log and runs
      every countdown in the app, so it goes on a ``BackgroundJob`` like the
      log-archive copy and the EQ probe before it. Filesystem work with a
      result delivered through an inbox is exactly what that seam is for.
    * Adopting the result is six rebinds, and it has to happen where the
      reads happen — between lines on the driver thread, not from the GUI
      thread while ``apply()`` runs.

    So a request lands in the inbox (the shape
    ``SharingCoordinator.enqueue_inbound`` and ``DumpWatcher.request_scan``
    use), a tick starts the parse, and a later tick adopts what came back —
    a few hundred ms after Apply, which nobody can perceive and no log line
    is lost to. Only the last request survives: two Applies in a row mean the
    second path is the one the user wants.
    """

    def __init__(self, book: SpellBook, *, spawn: Spawn | None = None) -> None:
        self._book = book
        self._job = BackgroundJob("spell-reload", spawn=spawn)
        self._lock = threading.Lock()
        self._pending: Path | None = None
        self._ready: SpellBook | None = None

    def request(self, path: Path) -> None:
        """Ask for the book to be reloaded from ``path``. Any thread."""
        with self._lock:
            self._pending = path

    @property
    def pending(self) -> Path | None:
        """A path asked for and not yet handed to the background job."""
        with self._lock:
            return self._pending

    def wait(self, timeout: float = 10.0) -> bool:
        """Block until no parse is in flight. True if it finished."""
        return self._job.wait(timeout)

    def tick(self, now: datetime) -> None:
        """Driver-tick hook. Never raises."""
        self._start_pending()
        self._adopt_ready()

    def _start_pending(self) -> None:
        with self._lock:
            path = self._pending
            if path is None:
                return
            self._pending = None
        if not self._job.submit(functools.partial(self._parse, path)):
            # A reload is already in flight; keep the request for the tick
            # after it lands rather than dropping it.
            with self._lock:
                self._pending = path

    def _parse(self, path: Path) -> None:
        """Background thread: read and index the database, hand it back.

        Touches no shared state but ``_ready`` (``npcs`` is an immutable
        frozenset that a reload never replaces), so nothing the driver thread
        is reading moves until the tick below adopts it.
        """
        try:
            book = load_spell_book(path, npcs=self._book.npcs)
        except Exception:
            logger.warning("could not reload the spell database from %s", path, exc_info=True)
            return
        with self._lock:
            self._ready = book

    def _adopt_ready(self) -> None:
        with self._lock:
            book, self._ready = self._ready, None
        if book is not None:
            self._book.adopt(book)
            logger.info("spell database switched to %s", book.source_path)


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
    test_alerts: AlertTestRunner
    mob_info: MobInfoState
    player_pet: PlayerPet
    speaker: _SwappableSpeaker
    sharing: SharingCoordinator
    sharing_client: SharingClient | None = None
    pigparse_api: PigParseApiClient | None = None
    net_worker: NetWorker | None = None
    player_tracker: PlayerTrackerHandler | None = None
    timer_persistence: TimerPersistenceHandler | None = None
    dps_persistence: DpsPersistenceHandler | None = None
    socials_sync: SocialSyncWatcher | None = None
    dumps: DumpLibrary | None = None
    dump_watcher: DumpWatcher | None = None
    inventory_upload: InventoryUploadHandler | None = None
    planner_api: P99PlannerClient | None = None
    spell_reload: _SpellBookReloader | None = None
    con_handler: ConHandler | None = None
    wiki: P99WikiClient | None = None
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

    def apply_dps_settings(self) -> None:
        """Push the DPS settings page onto the live tracker — the seam the
        settings window calls on Apply so the counting rules change without a
        restart (the tracker outlives every settings window)."""
        dps = self.settings.dps
        self.fights.configure(
            damage_sources=dps.damage_sources,
            fight_retention_s=dps.fight_retention_seconds,
            trailing_window_s=dps.trailing_window_seconds,
            session_min_fight_s=dps.session_min_fight_seconds,
            spell_credit_window_s=dps.spell_credit_window_seconds,
            count_pet_damage=dps.count_pet_damage,
        )

    def dps_best_owner(self) -> object | None:
        """Which character the DPS meter's lifetime best belongs to (#83).

        An opaque token for ``reset_dps_best`` — captured before a
        confirmation dialog and handed back after it, so a reset the user
        agreed to cannot land on whoever the driver switched to while they
        were reading it.
        """
        if self.dps_persistence is None:
            return None
        return self.dps_persistence.best_owner()

    def reset_dps_best(self, expect: object) -> None:
        """Clear the lifetime best on the driver thread, refusing if stale.

        Goes through ``submit_to_driver`` rather than mutating from the GUI
        thread: the check and the reset have to be one step with respect to
        the player-change pair, which runs there. With no driver thread
        running, that seam runs the closure inline — which is exactly right,
        because then there is nothing to race with.

        Fire-and-forget by design. The outcome is not returned to this caller
        because this caller is not the one that needs it and is no longer
        waiting — the command drains up to a poll interval later. The handler
        publishes ``DpsBestResetEvent`` instead, which reaches the window the
        one way the driver thread may reach it.
        """
        handler = self.dps_persistence
        if handler is None:
            # No profile persistence wired (a Backend built without it): there
            # is no character to bind to and nothing on disk to protect.
            self.fights.reset_best()
            return
        self.driver.submit_to_driver(lambda: handler.reset_best(expect), label="dps:reset-best")

    def apply_overlay_timings(self) -> None:
        """Push the overlay durations onto the live trigger engine — the
        Qt-free half of the seam the settings window calls on Apply (its
        callback re-times the overlay window's own timers next). The engine
        outlives every settings window, so this is an assignment rather than
        a rebuild; deliberately not folded into the appearance callback,
        which is also the skin picker's preview path."""
        self.trigger_engine.display_text_seconds = self.settings.general.overlay_text_seconds

    def apply_upload_target(self) -> None:
        """Build whatever the newly-picked dump destination needs and push it
        onto the live uploader — the seam the settings window calls on Apply.

        ``dumps.upload_target`` was already read live by the handler; what was
        not live was the plumbing that read depends on, decided once in
        ``build_backend``, so picking a destination mid-session left
        ``api``/``planner``/``submit`` at None and every upload silently
        no-opped (#68). This closes it for the Discord login too: the token
        rides in a per-request header, so logging in and picking pigparse in
        one Apply now works without a restart.

        **Nothing is ever torn down here.** ``stop()`` closes these at quit
        and an in-flight p99planner claim or PUT would be lost with them, so
        switching away leaves the client idle rather than killing it — which
        costs nothing, because the target is what gates a send, not the
        client. That gate is read twice: ``accepts()`` before anything is
        queued, and ``InventoryUploadHandler._still_targeting`` on the worker
        thread just before the request leaves, so a dump already sitting in
        the queue when the user switches off is dropped rather than sent.

        Deliberately narrow: the seven handlers that publish on *sharing's*
        behalf are not rewired, because a REST client built for an upload
        destination is not permission to share a location (#69).
        """
        target = self.settings.dumps.upload_target
        if target == "p99planner" and self.planner_api is None:
            self.planner_api = P99PlannerClient()
        if target == "pigparse" and self.pigparse_api is None:
            self.pigparse_api = PigParseApiClient(self.settings.sharing.pigparse_api_url)
        if target != "off" and self.net_worker is None:
            # start() ran at launch and will not run again, so this one starts
            # its own thread.
            self.net_worker = NetWorker(deliver=self.sharing.enqueue_inbound)
            self.net_worker.start()
        if self.inventory_upload is not None:
            self.inventory_upload.api = self.pigparse_api
            self.inventory_upload.planner = self.planner_api
            self.inventory_upload.submit = (
                self.net_worker.submit if self.net_worker is not None else None
            )

    def apply_mobinfo_settings(self) -> None:
        """Build what the Mob Info wiki lookup needs and push it onto the live
        ConHandler — the seam the settings window calls on Apply (#113).

        Same shape as ``apply_upload_target``, and for the same reason: the
        handler reads ``show_image`` live through a callable, but the client
        and the worker thread it needs were decided once in ``build_backend``,
        so turning the lookup on mid-session would otherwise do nothing until
        a restart. Nothing is torn down — turning it off drops the handler's
        reference, which is what stops the fetch; the client is idle after
        that and ``stop()`` closes the worker at quit.
        """
        if self.con_handler is None:  # pragma: no cover - always wired
            return
        if self.settings.mobinfo.wiki_details:
            if self.wiki is None:
                self.wiki = P99WikiClient(
                    zones=self.zones, image_cache_dir=ensure_wiki_image_cache_dir()
                )
            if self.net_worker is None:
                # start() ran at launch and will not run again (see
                # apply_upload_target), so this one starts its own thread.
                self.net_worker = NetWorker(deliver=self.sharing.enqueue_inbound)
                self.net_worker.start()
            self.con_handler.wiki = self.wiki
            self.con_handler.wiki_submit = self.net_worker.submit
        else:
            self.con_handler.wiki = None
            self.con_handler.wiki_submit = None

    def reload_spell_book(self) -> bool:
        """Re-resolve the spell database and schedule a reload if it moved.

        Called when the EQ install directory changes. Every other consumer of
        that setting was already live; this one silently stayed on whatever
        it resolved at launch, which bites hardest on first run — log
        directory and install directory get set in the same visit and spell
        durations stay on the bundled database all session (#70).

        Returns whether a reload was *scheduled* — the parse runs off-thread
        and the swap lands on a driver tick a few hundred ms later (see
        :class:`_SpellBookReloader`). "Same file" is a visible answer rather
        than an inferred one because pointing at an install with no
        ``spells_us.txt`` resolves straight back to the bundled copy, and
        re-parsing 8k lines to arrive where we already are is pure cost.
        """
        if self.spell_reload is None:  # pragma: no cover - always wired
            return False
        path = _spells_path(self.settings)
        if _same_file(path, self.spells.source_path):
            return False
        self.spell_reload.request(path)
        return True

    def apply_sharing_mode(self) -> None:
        """Push the sharing mode onto the live coordinator — the seam the
        settings window calls on Apply. Only "off" applies live (see
        ``SharingCoordinator.apply_mode``); turning sharing on still needs a
        restart. Clearing our own handle keeps ``stop()`` from stopping a
        client the coordinator has already dropped."""
        if self.sharing.apply_mode():
            self.sharing_client = None

    def stop(self) -> None:
        self.driver.stop()
        if self.timer_persistence is not None:
            # After the driver thread is joined: quit-time seconds-left for the
            # you_spells store (the app's aboutToQuit save flush runs next).
            # A no-op if the character camped — that snapshot was taken then,
            # and the rows are already off screen (#120).
            self.timer_persistence.export_now()
        if self.sharing_client is not None:
            self.sharing_client.stop()
        if self.net_worker is not None:
            self.net_worker.stop()
        if self.pigparse_api is not None:
            self.pigparse_api.close()
        if self.planner_api is not None:
            self.planner_api.close()


def _spells_path(settings: Settings) -> Path:
    """Resolve the spell database, and say out loud which one won (#177).

    Which file this is decides every same-message guess the matcher makes, and
    a user running P99's own spells_us.txt has a candidate list this repo
    cannot reproduce — so a report of a wrong spell name is only actionable
    once the log says whether it came from the install or the bundled copy.
    Logged at every resolve rather than once per process: the setting is live
    (#70), so the answer can change mid-session.
    """
    install = settings.general.eq_install_dir
    if install:
        candidate = Path(install) / "spells_us.txt"
        if candidate.is_file():
            logger.info("spell database: EQ install %s", candidate)
            return candidate
        if candidate.parent.exists():
            logger.info("spell database: no spells_us.txt in %s; using the bundled copy", install)
    bundled = Path("data/spells/spells_us.txt")  # bundled fallback (repo/app root)
    logger.info("spell database: bundled %s", bundled)
    return bundled


def _same_file(path: Path, other: Path | None) -> bool:
    """Whether two spell-database paths name the same file.

    Resolved, because the bundled fallback above is relative to the app's CWD
    while an install path is absolute — comparing them raw would report a move
    that isn't one.
    """
    if other is None:
        return False
    try:
        return path.resolve() == other.resolve()
    except OSError:  # pragma: no cover - defensive (unreadable parent)
        return path == other


def build_backend(settings: Settings, speaker=None, request_save=None) -> Backend:
    """``request_save`` is the app's DebouncedSaver.request_save (thread-safe);
    driver-thread handlers use it to persist per-character profile changes."""
    bus = EventBus()
    player = ActivePlayer()
    zones = load_zone_database()
    spells = load_spell_book(_spells_path(settings))
    spell_reload = _SpellBookReloader(spells)
    timers = TimersService()

    ctx = ParseContext(bus=bus, player=player, spells=spells, zones=zones, settings=settings)
    parsers = build_parser_chain()
    pipeline = LogPipeline(parsers, ctx)
    driver = LogDriver(Path(settings.general.eq_log_dir), pipeline, bus, player)
    # The one parser the app has to hold a handle on: its 6 s camp delay is
    # resolved on the driver tick (see core/parsers/camp.py) so that CampEvent
    # subscribers may touch TimersService.
    camp_parser = next(p for p in parsers if isinstance(p, CampParser))

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

    fights = FightTracker(
        damage_sources=settings.dps.damage_sources,
        fight_retention_s=settings.dps.fight_retention_seconds,
        trailing_window_s=settings.dps.trailing_window_seconds,
        session_min_fight_s=settings.dps.session_min_fight_seconds,
        spell_credit_window_s=settings.dps.spell_credit_window_seconds,
        count_pet_damage=settings.dps.count_pet_damage,
    )
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
    # A dump upload destination needs a worker thread even with sharing
    # off: p99planner has nothing to do with location sharing, and requiring
    # sharing to be on to upload would be a strange thing to explain.
    planner_api: P99PlannerClient | None = None
    if settings.dumps.upload_target == "p99planner":
        planner_api = P99PlannerClient()
    if settings.dumps.upload_target == "pigparse" and pigparse_api is None:
        # Uploading to pigparse.org has nothing to do with sharing your
        # location through it, and the destination picker offers them
        # independently — so the REST client cannot be sharing's to own.
        pigparse_api = PigParseApiClient(settings.sharing.pigparse_api_url)
    if net_worker is None and settings.dumps.upload_target != "off":
        net_worker = NetWorker(deliver=sharing.enqueue_inbound)
    # Mob Info's wiki lookup is a third feature that needs a thread and has
    # nothing to do with either of the two above: wiki.project1999.com is not
    # pigparse's API, so sharing does not gate it (#113).
    wiki_client: P99WikiClient | None = None
    if settings.mobinfo.wiki_details:
        wiki_client = P99WikiClient(zones=zones, image_cache_dir=ensure_wiki_image_cache_dir())
        if net_worker is None:
            net_worker = NetWorker(deliver=sharing.enqueue_inbound)

    sharing.set_client(sharing_client)
    submit = net_worker.submit if net_worker is not None else None
    # Two submit handles, because two different decisions gate them. Everything
    # that talks to pigparse.org ON SHARING'S BEHALF goes through the gated one
    # so turning sharing off silences it live (#69) — and so does a session
    # started with sharing off but a pigparse dump-upload target, which builds
    # the REST client for the uploader and used to hand it to these seven too.
    # InventoryUploadHandler keeps the raw handle: its gate is
    # dumps.upload_target.
    sharing_submit = sharing_gated_submit(submit, sharing.pigparse_rest_allowed)

    def timer_recast() -> str:
        """The active character's PlayerInfo.TimerRecastSetting."""
        server_key = player.server_key
        info = find_player(settings, player.name, server_key) if server_key else None
        return info.timer_recast if info is not None else "RestartCurrentTimer"

    # ONE poll of the EQ directory for /outputfile dumps, with two consumers:
    # this library keeps per-character history of both dump kinds, and
    # InventoryUploadHandler below hangs off its on_fresh_dump hook to do the
    # upload to whichever site is picked. EQTool has a single service
    # doing both; splitting it is what stops two ticks racing over the same
    # file. Consequence: the upload rides on dumps.auto_import being on.
    dumps = DumpLibrary(ensure_dumps_dir())
    inventory_upload = InventoryUploadHandler(
        bus,
        player,
        dumps,
        get_target=lambda: settings.dumps.upload_target,
        get_token=lambda: settings.pigparse_account.api_token,
        # Replaces the old watcher's startup priming: a dump the player took
        # before this session started is not news worth uploading.
        session_start=datetime.now(),
        api=pigparse_api,
        planner=planner_api,
        submit=submit,
    )

    player_tracker = PlayerTrackerHandler(bus, player, api=pigparse_api, submit=sharing_submit)
    # Two submits on purpose: the pigparse pricing leg is sharing's and takes
    # the gated one; the wiki leg is not, and takes the raw worker (#113).
    con_handler = ConHandler(
        bus,
        player,
        zones,
        player_pet=player_pet,
        mob_info=mob_info,
        api=pigparse_api,
        submit=sharing_submit,
        wiki=wiki_client,
        wiki_submit=submit,
        want_image=lambda: settings.mobinfo.show_image,
    )
    profile_handler = PlayerProfileHandler(bus, player, settings, request_save=request_save)
    # Held rather than built inline: the DPS window's Reset best action has to
    # ask it who the current best belongs to, and hand that back (#83).
    dps_persistence = DpsPersistenceHandler(
        bus, player, settings, fights, request_save=request_save
    )
    # Constructed (= subscribed) after PlayerProfileHandler: restore-on-player-
    # change needs the profile's class/level already loaded into ActivePlayer.
    timer_persistence = TimerPersistenceHandler(
        bus,
        player,
        settings,
        timers,
        spells,
        request_save=request_save,
        # Seconds-left is measured against the log's own clock, so a client
        # that stopped writing (linkdead) freezes the snapshot instead of
        # letting the wall clock drain it to nothing (#120).
        log_clock=lambda: pipeline.last_entry_time,
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
        DpsHandler(bus, player, fights, player_pet=player_pet, zones=zones),
        dps_persistence,
        SpawnTimerHandler(bus, player, timers, zones, npcs=npcs, timer_recast=timer_recast),
        RespawnExpiryNotifier(timers, speaker, settings.spellwindow),
        TimerWindowNotifier(bus, timers),
        CorpseWaypointHandler(bus, player),
        RandomRollHandler(bus, player, timers),
        FTEHandler(bus, player, timers, speaker=speaker, api=pigparse_api, submit=sharing_submit),
        QuakeHandler(bus, player, speaker=speaker, api=pigparse_api, submit=sharing_submit),
        RingWarHandler(bus, player, timers),
        BoatHandler(bus, player, timers, zones, api=pigparse_api, submit=sharing_submit),
        PetHandler(bus, player, pets, player_pet=player_pet),
        con_handler,
        ZoneActivityHandler(bus, player, zones, api=pigparse_api, submit=sharing_submit),
        DisciplineCooldownHandler(bus, player, timers),
        MendWoundsHandler(bus, player, timers),
        AbilityCooldownHandler(bus, player, spells, timers),
        CompleteHealCommsHandler(
            bus,
            player,
            npcs=npcs,
            ch_chain_tag=lambda: settings.general.ch_chain_tag,
            cadence_enabled=lambda: settings.general.ch_cadence_indicator,
            cadence_patterns=lambda: settings.general.ch_cadence_patterns,
        ),
        CompleteHealHandler(bus, player, speaker=speaker),
        BardCountHandler(
            bus,
            player,
            speaker=speaker,
            timers=timers,
            enabled=lambda: settings.general.bard_count_enabled,
        ),
        RootBreakHandler(
            bus,
            player,
            speaker=speaker,
            overlay_enabled=lambda: settings.general.root_break_overlay,
            audio_enabled=lambda: settings.general.root_break_audio,
        ),
        DeathLoopHandler(bus, player, speaker=speaker),
        GroupLeaderHandler(bus, player),
        inventory_upload,
    ]
    api_timers = ApiTimersService(timers, zones, player, api=pigparse_api, submit=sharing_submit)

    # The poll itself (see the library above for why there is only one).
    dump_watcher = DumpWatcher(
        dumps,
        get_eq_dir=lambda: (
            Path(settings.general.eq_install_dir) if settings.general.eq_install_dir else None
        ),
        is_enabled=lambda: settings.dumps.auto_import,
        is_update_enabled=lambda: settings.dumps.auto_update,
        get_keep=lambda: settings.dumps.keep_per_character,
        bus=bus,
        # The upload trigger, deliberately NOT the bus events the library
        # publishes: those say "a snapshot was stored", which auto_update can
        # veto and a hand-picked import can raise. Neither should decide what
        # leaves the machine. See InventoryUploadHandler.on_fresh_dump.
        on_fresh_dump=inventory_upload.on_fresh_dump,
    )

    socials_sync = SocialSyncWatcher(
        get_eq_dir=lambda: (
            Path(settings.general.eq_install_dir) if settings.general.eq_install_dir else None
        ),
        get_store_dir=ensure_socials_dir,
        is_enabled=lambda: settings.general.socials_autosync,
    )

    archiver = LogArchiveService(
        get_log_dir=lambda: settings.general.eq_log_dir,
        is_enabled=lambda: settings.general.log_archive_enabled,
        get_threshold_mb=lambda: settings.general.log_archive_size_mb,
        # Emptying a log and resetting whoever tails it have to be one step —
        # the service's tick runs on the driver thread precisely so they can
        # be. See core/logarchive.py's docstring.
        on_rotated=driver.note_log_rotated,
    )

    buff_warner = BuffFadeWarner(bus, timers, speaker, settings.spellwindow)
    # First: a reload asked for by the last Apply lands before this tick's
    # handlers read the book.
    driver.on_tick.append(spell_reload.tick)
    # Before timers.tick: a completed camp hides this character's rows, and the
    # countdown pass should run on what is left.
    driver.on_tick.append(camp_parser.tick)
    driver.on_tick.append(timers.tick)
    driver.on_tick.append(buff_warner.tick)
    driver.on_tick.append(engine.tick)
    driver.on_tick.append(fights.tick)
    driver.on_tick.append(archiver.tick)
    driver.on_tick.append(sharing.tick)
    driver.on_tick.append(api_timers.tick)
    driver.on_tick.append(player_tracker.tick)
    driver.on_tick.append(socials_sync.tick)
    driver.on_tick.append(dump_watcher.tick)

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
        test_alerts=AlertTestRunner(pipeline, timers, driver.submit_to_driver),
        mob_info=mob_info,
        player_pet=player_pet,
        speaker=speaker,
        sharing=sharing,
        sharing_client=sharing_client,
        pigparse_api=pigparse_api,
        net_worker=net_worker,
        player_tracker=player_tracker,
        timer_persistence=timer_persistence,
        dps_persistence=dps_persistence,
        socials_sync=socials_sync,
        dumps=dumps,
        dump_watcher=dump_watcher,
        inventory_upload=inventory_upload,
        planner_api=planner_api,
        spell_reload=spell_reload,
        con_handler=con_handler,
        wiki=wiki_client,
        _retained=[chat_commands, window_commands, sink, *handlers],
    )
