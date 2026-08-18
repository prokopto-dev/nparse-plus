"""HostPluginContext — the app's implementation of the SDK PluginContext.

Every callback a plugin registers is wrapped in a try/except tagged with the
plugin id so a broken plugin can log loudly but never break bus dispatch,
the tick loop, or the parser chain. Registrations are tracked so a plugin
that fails mid-``activate`` can be unwound.

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
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from nparseplus.core.parsers.base import LineParser
from nparseplus.core.plugins.storage import JsonPluginStorage
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


class HostPluginContext:
    """Capability object handed to ``NParsePlugin.activate`` (SDK protocol)."""

    def __init__(
        self,
        meta: PluginMeta,
        backend: Backend,
        app_version: str,
        storage: JsonPluginStorage,
        owned_net: _OwnedNet,
    ) -> None:
        self._meta = meta
        self._backend = backend
        self._app_version = app_version
        self._storage = storage
        self._owned_net = owned_net
        self._logger = logging.getLogger(f"nparseplus.plugins.{meta.id}")
        # Registrations tracked for unwind if activate fails partway.
        self._unsubscribes: list[Unsubscribe] = []
        self._ticks: list[Callable[[datetime], None]] = []
        self._parsers: list[LineParser] = []
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
        return self._backend.timers

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

        def _guarded(event: Any) -> None:
            try:
                fn(event)
            except Exception:
                plugin_logger.exception(
                    "handler for %s raised (plugin %s)", event_type.__name__, self._meta.id
                )

        unsubscribe = self._backend.bus.subscribe(event_type, _guarded)
        self._unsubscribes.append(unsubscribe)
        return unsubscribe

    def add_parser(self, parser: LineParser) -> None:
        # The pipeline already guards each parser's handle() with try/except.
        self._backend.pipeline.append_parser(parser)
        self._parsers.append(parser)

    def add_tick(self, fn: Callable[[datetime], None]) -> None:
        plugin_logger = self._logger

        def _guarded(now: datetime) -> None:
            try:
                fn(now)
            except Exception:
                plugin_logger.exception("tick raised (plugin %s)", self._meta.id)

        # Supervised: the driver times this callback and drops it if it keeps
        # blowing the budget, because a blocking tick freezes log tailing,
        # every timer and the sharing inbox along with it.
        self._backend.driver.add_supervised_tick(
            _guarded,
            label=f"plugin {self._meta.id}",
            on_dropped=self._note_tick_dropped,
        )
        self._ticks.append(_guarded)

    def _note_tick_dropped(self, reason: str) -> None:
        """Driver-thread callback; a plain string the GUI reads on refresh."""
        self.tick_dropped = reason

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
        ``ctx.timers.remove_row(row)`` is the way to take it out again.
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
        return self._backend.timers.add_timer(row, allow_duplicates=allow_duplicates)

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
