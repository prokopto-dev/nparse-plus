"""HostPluginContext — the app's implementation of the SDK PluginContext.

Every callback a plugin registers is wrapped in a try/except tagged with the
plugin id so a broken plugin can log loudly but never break bus dispatch,
the tick loop, or the parser chain. Registrations are tracked so a plugin
that fails mid-``activate`` can be unwound.

Since #132 those same wrappers are where a plugin's callbacks are timed.
The instrumentation is deliberately built into the wrapper that already
existed rather than layered over it: a plugin callback was already one
indirection away from the bus, and this keeps it at one. See
``core/plugins/telemetry.py`` for what the numbers mean and why collection
being off costs a single attribute read.

``_OwnedNet`` covers the sharing-off case: when the backend built no
NetWorker / PigParse client (``settings.sharing.mode == "off"``), one lazily
constructed pair — shared by all plugins, owned and closed by the host —
delivers apply-closures through ``sharing.enqueue_inbound``, whose inbox the
coordinator drains on every driver tick regardless of sharing mode. Plugins
therefore never see ``None``.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta
from pathlib import Path
from time import perf_counter
from typing import TYPE_CHECKING, Any

from nparseplus.core.parsers.base import LineParser, ParseContext
from nparseplus.core.plugins.storage import JsonPluginStorage
from nparseplus.core.plugins.telemetry import Channel, PluginMetrics
from nparseplus.net.pigparse_api import PigParseApiClient
from nparseplus.net.worker import NetWorker
from nparseplus_sdk import (
    SDK_VERSION,
    PluginMeta,
    PluginSettingsPageSpec,
    PluginWindowSpec,
    Unsubscribe,
)

if TYPE_CHECKING:
    from nparseplus.composition import Backend

logger = logging.getLogger(__name__)


class _OwnedNet:
    """At most one NetWorker + PigParse client for all plugins combined.

    The lazy construction is locked because plugins legitimately reach these
    from more than one thread — a driver tick and a window's Qt timer, say.
    Two racing threads would otherwise each build a client (or start a
    daemon worker) and ``close`` would only ever release the last one,
    leaking an httpx pool and a thread for the life of the process.
    """

    def __init__(self, backend: Backend) -> None:
        self._backend = backend
        self._lock = threading.Lock()
        self._worker: NetWorker | None = None
        self._api: PigParseApiClient | None = None

    def submit(
        self,
        fetch: Callable[[], Any],
        apply: Callable[[Any], None] | None = None,
    ) -> None:
        if self._backend.net_worker is not None:
            self._backend.net_worker.submit(fetch, apply)
            return
        with self._lock:
            if self._worker is None:
                self._worker = NetWorker(deliver=self._backend.sharing.enqueue_inbound)
                self._worker.start()
            worker = self._worker
        worker.submit(fetch, apply)

    @property
    def api(self) -> Any:
        if self._backend.pigparse_api is not None:
            return self._backend.pigparse_api
        with self._lock:
            if self._api is None:
                self._api = PigParseApiClient(self._backend.settings.sharing.pigparse_api_url)
            return self._api

    def close(self) -> None:
        with self._lock:
            worker, self._worker = self._worker, None
            api, self._api = self._api, None
        if worker is not None:
            worker.stop()
        if api is not None:
            api.close()


class _PluginTimers:
    """``ctx.timers`` for one plugin: rows tagged, mutations kept on-thread.

    A thin facade over the real ``TimersService``. Reads and observer lists
    pass straight through (``snapshot``, ``find``, ``on_change``, ``tick``);
    the mutations it wraps do two things the raw service cannot do for a
    plugin.

    **They stamp ``row.owner``.** That is what lets ``PluginHost`` take a
    plugin's countdowns off the Timers window when the user disables it
    (#45): ``unwind()`` reverses *registrations*, and rows are data the
    plugin put in a user-visible store, so nothing there could find them
    again. Stamped here rather than asked of the plugin because a plugin
    cannot be relied on to do it, and the row it hands over is the one
    moment the host knows whose it is. ``add_counter`` deliberately stamps
    only the row coming in: incrementing a tally the app (or another plugin)
    already owns must not transfer it.

    **They are applied on the driver thread.** ``TimersService`` is not
    thread-safe and its ``on_change`` observers expect that thread, and
    since #45 a plugin's ``activate``/``deactivate`` can run on the GUI
    thread — the settings window enabling an add-on mid-session. So a
    mutation from anywhere else is routed through
    ``LogDriver.submit_to_driver`` and lands at the next loop boundary,
    exactly like the host's own ``remove_owner`` sweep.

    A call that is already on the driver thread — a plugin's handler, tick
    or parser, and every call at startup before the loop exists — is applied
    immediately and returns what the service returned, which is what keeps
    the ordinary path identical to before. Only a *deferred* call cannot
    know the service's answer, and there it answers what the caller asked
    for: its own row back, and for ``remove_row`` whether the row was in the
    store when asked. One consequence worth knowing: an ``add_counter``
    deferred from another thread returns the caller's row, so a merge into
    an existing tally is not visible on it — read the store from a tick.
    """

    def __init__(self, timers: Any, driver: Any, plugin_id: str) -> None:
        self._timers = timers
        self._driver = driver
        self._plugin_id = plugin_id

    def __getattr__(self, name: str) -> Any:
        return getattr(self._timers, name)

    def add_spell(self, row: Any, overwrite: bool = True) -> Any:
        row.owner = self._plugin_id
        return self._apply(lambda: self._timers.add_spell(row, overwrite), row, "add_spell")

    def add_timer(self, row: Any, allow_duplicates: bool = False) -> Any:
        row.owner = self._plugin_id
        return self._apply(lambda: self._timers.add_timer(row, allow_duplicates), row, "add_timer")

    def add_counter(self, row: Any) -> Any:
        row.owner = self._plugin_id
        return self._apply(lambda: self._timers.add_counter(row), row, "add_counter")

    def add_roll(self, row: Any) -> Any:
        row.owner = self._plugin_id
        return self._apply(lambda: self._timers.add_roll(row), row, "add_roll")

    def remove_row(self, row: Any) -> Any:
        # The snapshot is a copy, so reading it off-thread is safe, and
        # "was the row there when you asked" is the honest answer available
        # to a removal that has not happened yet.
        return self._apply(
            lambda: self._timers.remove_row(row),
            row in self._timers.snapshot(),
            "remove_row",
        )

    def _apply(self, mutate: Callable[[], Any], deferred: Any, what: str) -> Any:
        """Run ``mutate`` now if this is the driver thread, else queue it.

        ``deferred`` is what to answer when it was queued — see the class
        docstring for why it is the caller's own value and not the
        service's.
        """
        if self._driver.on_driver_thread:
            return mutate()

        def run() -> None:
            mutate()

        self._driver.submit_to_driver(run, label=f"plugin {self._plugin_id} {what}")
        return deferred


class _MeasuredParser:
    """Times one plugin parser, and is otherwise invisible to the chain.

    Wrapping rather than timing inside ``LogPipeline.process`` is what keeps
    the app's own parsers — nine tenths of the chain, run on every line —
    exactly as fast as they were: the pipeline has no idea which parsers
    belong to a plugin, and asking it per line would be the cost this is
    supposed to measure.

    Exceptions are deliberately allowed through to the pipeline's own guard,
    which is the thing that decides a raising parser does not consume the
    line. ``parser_label`` is how that guard's log line still names the real
    parser (``core.parsers.base.describe_parser``).
    """

    __slots__ = ("_channel", "_parser", "parser_label")

    def __init__(self, parser: LineParser, channel: Channel, plugin_id: str) -> None:
        self._parser = parser
        self._channel = channel
        self.parser_label = f"{type(parser).__name__} (plugin {plugin_id})"

    def handle(self, line: Any, ctx: ParseContext) -> bool:
        channel = self._channel
        if not channel.enabled:
            return self._parser.handle(line, ctx)
        started = perf_counter()
        try:
            return self._parser.handle(line, ctx)
        except Exception:
            channel.note_error()
            raise
        finally:
            channel.record(perf_counter() - started, started)


class HostPluginContext:
    """Capability object handed to ``NParsePlugin.activate`` (SDK protocol)."""

    def __init__(
        self,
        meta: PluginMeta,
        backend: Backend,
        app_version: str,
        storage: JsonPluginStorage,
        owned_net: _OwnedNet,
        metrics: PluginMetrics | None = None,
    ) -> None:
        self._meta = meta
        self._backend = backend
        self._app_version = app_version
        self._storage = storage
        self._owned_net = owned_net
        # None only in the fakes and older tests that build a context by
        # hand; the host always supplies one. Absent, every callback below
        # takes exactly the path it took before #132.
        self._metrics = metrics
        self._logger = logging.getLogger(f"nparseplus.plugins.{meta.id}")
        # Registrations tracked for unwind if activate fails partway.
        self._unsubscribes: list[Unsubscribe] = []
        self._ticks: list[Callable[[datetime], None]] = []
        self._parsers: list[LineParser] = []
        # Built on first use and kept, so a plugin that stores ctx.timers
        # keeps one object (see _PluginTimers).
        self._plugin_timers: _PluginTimers | None = None
        self.window_specs: list[PluginWindowSpec] = []
        self.page_specs: list[PluginSettingsPageSpec] = []
        # Set (on the driver thread) if the driver evicted one of this
        # plugin's ticks for overrunning its budget; the manager page shows
        # it. A plain str|None so core stays Qt-free and the GUI just reads.
        self.tick_dropped: str | None = None

    # --- identity / environment -------------------------------------------
    @property
    def meta(self) -> PluginMeta:
        return self._meta

    @property
    def app_version(self) -> str:
        return self._app_version

    @property
    def sdk_version(self) -> str:
        return SDK_VERSION

    @property
    def logger(self) -> logging.Logger:
        return self._logger

    @property
    def storage(self) -> JsonPluginStorage:
        return self._storage

    # --- the EQ install ----------------------------------------------------
    @property
    def eq_dir(self) -> Path | None:
        """The configured EQ install directory, or None when unset.

        Read through to the settings on every access rather than captured at
        construction: every other consumer of ``general.eq_install_dir`` went
        live in #70, and a plugin holding a launch-time snapshot would be the
        one thing still pointing at the old install after the user changes it.

        Narrower than what plugins can already reach — ``PluginWindowContext``
        hands window factories the whole mutable ``Settings`` root — so this
        is the same capability through a door ``activate()`` can use, not a
        new one (#123).
        """
        configured = self._backend.settings.general.eq_install_dir
        return Path(configured) if configured else None

    def eq_is_running(self) -> bool:
        """Whether the EQ client appears to be running (best effort).

        Spawns ``pgrep`` (~18 ms), so the SDK docstring tells plugins to keep
        it off their ticks; exposed rather than left to each plugin precisely
        because #88 was this call landing on the wrong thread.
        """
        from nparseplus.core.eqprocess import eq_is_running

        return eq_is_running()

    # --- backend capabilities ---------------------------------------------
    @property
    def timers(self) -> Any:
        """The host TimersService, wrapped so rows added here are tagged.

        The wrapper is invisible to a plugin — everything it does not stamp
        passes through — and it is what makes a plugin's countdowns go away
        with the plugin when the user disables it (#45).
        """
        if self._plugin_timers is None:
            self._plugin_timers = _PluginTimers(
                self._backend.timers, self._backend.driver, self._meta.id
            )
        return self._plugin_timers

    @property
    def player(self) -> Any:
        return self._backend.player

    @property
    def speaker(self) -> Any:
        return self._backend.speaker

    @property
    def pigparse(self) -> Any:
        return self._owned_net.api

    # --- registration ------------------------------------------------------
    def subscribe(self, event_type: type[Any], fn: Callable[[Any], None]) -> Unsubscribe:
        plugin_logger = self._logger
        plugin_id = self._meta.id
        metrics = self._metrics

        if metrics is None:

            def _guarded(event: Any) -> None:
                try:
                    fn(event)
                except Exception:
                    plugin_logger.exception(
                        "handler for %s raised (plugin %s)", event_type.__name__, plugin_id
                    )

        else:
            channel = metrics.handlers

            def _guarded(event: Any) -> None:
                # The gate, and the whole cost of collection being off: one
                # attribute read on a __slots__ object before any clock.
                if not channel.enabled:
                    try:
                        fn(event)
                    except Exception:
                        plugin_logger.exception(
                            "handler for %s raised (plugin %s)", event_type.__name__, plugin_id
                        )
                    return
                started = perf_counter()
                try:
                    fn(event)
                except Exception:
                    channel.note_error()
                    plugin_logger.exception(
                        "handler for %s raised (plugin %s)", event_type.__name__, plugin_id
                    )
                finally:
                    # In the finally so a raising handler still costs what it
                    # cost: a plugin that throws on every event is exactly the
                    # one whose time the user needs to see.
                    channel.record(perf_counter() - started, started)

        unsubscribe = self._backend.bus.subscribe(event_type, _guarded)
        self._unsubscribes.append(unsubscribe)
        return unsubscribe

    def add_parser(self, parser: LineParser) -> None:
        # The pipeline already guards each parser's handle() with try/except.
        # What goes into the chain is the wrapper when there is one, and it is
        # what `unwind` must take back out, so track that object and not the
        # plugin's.
        registered: LineParser = parser
        if self._metrics is not None:
            registered = _MeasuredParser(parser, self._metrics.parsers, self._meta.id)
        self._backend.pipeline.append_parser(registered)
        self._parsers.append(registered)

    def add_tick(self, fn: Callable[[datetime], None]) -> None:
        plugin_logger = self._logger

        def _guarded(now: datetime) -> None:
            try:
                fn(now)
            except Exception:
                plugin_logger.exception("tick raised (plugin %s)", self._meta.id)

        # Supervised: the driver times this callback and drops it if it keeps
        # blowing the budget, because a blocking tick freezes log tailing,
        # every timer and the sharing inbox along with it. Since #132 the
        # elapsed value that budget already computes is also handed to the
        # tick channel — no second clock read, which is why tick timing has
        # no gate to turn off.
        record = self._metrics.ticks.record if self._metrics is not None else None
        self._backend.driver.add_supervised_tick(
            _guarded,
            label=f"plugin {self._meta.id}",
            on_dropped=self._note_tick_dropped,
            on_duration=record,
        )
        self._ticks.append(_guarded)

    def _note_tick_dropped(self, reason: str) -> None:
        """Driver-thread callback; a plain string the GUI reads on refresh."""
        self.tick_dropped = reason
        if self._metrics is not None:
            # The one thing genuinely "dropped" in this release: a tick the
            # watchdog evicted. Counted here rather than in the driver so the
            # driver keeps knowing nothing about plugin bookkeeping.
            self._metrics.ticks.note_dropped()

    def submit(
        self,
        fetch: Callable[[], Any],
        apply: Callable[[Any], None] | None = None,
    ) -> None:
        plugin_logger = self._logger
        guarded_apply: Callable[[Any], None] | None = None
        if apply is not None:

            def guarded_apply(result: Any) -> None:
                try:
                    apply(result)
                except Exception:
                    plugin_logger.exception("submit apply raised (plugin %s)", self._meta.id)

        self._owned_net.submit(fetch, guarded_apply)

    def add_window(self, spec: PluginWindowSpec) -> None:
        self.window_specs.append(spec)

    def add_settings_page(self, spec: PluginSettingsPageSpec) -> None:
        self.page_specs.append(spec)

    # --- timers -----------------------------------------------------------
    def add_window_timer(
        self,
        name: str,
        *,
        group: str,
        started_at: datetime,
        base_seconds: float,
        window_seconds: float,
        allow_duplicates: bool = False,
    ) -> Any:
        """Arm a variable respawn ("pop") window timer (#125); returns the row.

        The return type stays ``Any`` on purpose: the host must not import
        ``WindowTimerLike``, so it keeps running against an installed SDK 1.1
        that has no such name.

        Deliberately NOT ``_guarded``. ``subscribe``/``add_tick`` wrap
        callbacks the *host* later invokes, where an exception has no plugin
        frame to land in; this is the plugin calling in, already inside its own
        guarded subscription or tick, so a ValidationError belongs in its
        frame where its traceback is useful.

        Deliberately NOT tracked in ``unwind()``. That reverses *registrations*;
        a timer row is data the plugin put in a user-visible store, exactly like
        ``ctx.timers.add_timer`` today. The plugin gets the row back, so
        ``ctx.timers.remove_row(row)`` is the way to take it out again — and,
        since #45, the row carries this plugin's id, so disabling the plugin
        takes it off the screen too (see ``_PluginTimers``).
        """
        from nparseplus.core.timers import TimerRow

        ends_at = started_at + timedelta(seconds=base_seconds)
        row = TimerRow(
            name=name,
            group=group,
            updated_at=started_at,
            ends_at=ends_at,
            total_duration_s=float(base_seconds),
            window_ends_at=ends_at + timedelta(seconds=window_seconds),
        )
        return self.timers.add_timer(row, allow_duplicates=allow_duplicates)

    def add_window_series(
        self,
        name: str,
        *,
        group: str,
        started_at: datetime,
        windows: Sequence[tuple[float, float]],
    ) -> list[Any]:
        """Arm every candidate window of one spawn (#125); returns the rows.

        Validated here rather than in the row model because these are rules
        about the SET, which no single row can see: at least one candidate,
        each window a real span, and the candidates in ascending order. An
        out-of-order table is a caller bug, and rendering it would silently
        mislabel which chance is which.

        The series key is derived, not random, so it is stable across a
        restore: the same call after a camp rebuilds the same identity.
        """
        from nparseplus.core.timers import TimerRow

        if not windows:
            raise ValueError("a window series needs at least one candidate window")
        previous_end: float | None = None
        for base_seconds, window_seconds in windows:
            if window_seconds <= 0:
                raise ValueError("every candidate window must be a positive span")
            if previous_end is not None and base_seconds < previous_end:
                raise ValueError("candidate windows must be in ascending, non-overlapping order")
            previous_end = base_seconds + window_seconds
        series = f"{group}|{name}|{started_at.isoformat()}"
        rows: list[Any] = []
        for index, (base_seconds, window_seconds) in enumerate(windows, start=1):
            ends_at = started_at + timedelta(seconds=base_seconds)
            row = TimerRow(
                name=name,
                group=group,
                updated_at=started_at,
                ends_at=ends_at,
                total_duration_s=float(base_seconds),
                window_ends_at=ends_at + timedelta(seconds=window_seconds),
                window_series=series,
                window_index=index,
                window_count=len(windows),
            )
            # Always duplicates-allowed: the candidates share a name by
            # design, so the default would make each one destroy the last.
            rows.append(self.timers.add_timer(row, allow_duplicates=True))
        return rows

    # --- host-side lifecycle ----------------------------------------------
    def unwind(self) -> None:
        """Best-effort removal of everything this plugin registered."""
        for unsubscribe in self._unsubscribes:
            try:
                unsubscribe()
            except Exception:
                logger.exception("unsubscribe failed for plugin %s", self._meta.id)
        self._unsubscribes.clear()
        for tick in self._ticks:
            # remove_tick also clears the driver's supervision record; a tick
            # already dropped for slowness unregisters without complaint.
            self._backend.driver.remove_tick(tick)
        self._ticks.clear()
        for parser in self._parsers:
            self._backend.pipeline.remove_parser(parser)
        self._parsers.clear()
        self.window_specs.clear()
        self.page_specs.clear()
        # The tick that earned this note is gone with the rest, so the note
        # has to go too: a plugin re-enabled in the same session would
        # otherwise inherit "tick disabled (too slow)" from the run before.
        self.tick_dropped = None
