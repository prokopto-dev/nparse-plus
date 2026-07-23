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

import contextlib
import logging
from collections.abc import Callable
from datetime import datetime
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
    """At most one NetWorker + PigParse client for all plugins combined."""

    def __init__(self, backend: Backend) -> None:
        self._backend = backend
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
        if self._worker is None:
            self._worker = NetWorker(deliver=self._backend.sharing.enqueue_inbound)
            self._worker.start()
        self._worker.submit(fetch, apply)

    @property
    def api(self) -> Any:
        if self._backend.pigparse_api is not None:
            return self._backend.pigparse_api
        if self._api is None:
            self._api = PigParseApiClient(self._backend.settings.sharing.pigparse_api_url)
        return self._api

    def close(self) -> None:
        if self._worker is not None:
            self._worker.stop()
            self._worker = None
        if self._api is not None:
            self._api.close()
            self._api = None


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

        self._backend.driver.on_tick.append(_guarded)
        self._ticks.append(_guarded)

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
            with contextlib.suppress(ValueError):
                self._backend.driver.on_tick.remove(tick)
        self._ticks.clear()
        for parser in self._parsers:
            self._backend.pipeline.remove_parser(parser)
        self._parsers.clear()
        self.window_specs.clear()
        self.page_specs.clear()
