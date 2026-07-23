"""PluginHost — owns the full plugin lifecycle for one app run.

Flow (all failures isolated per plugin; the app never crashes on a plugin):

1. ``discover_and_load()`` — pre-Qt, pre-driver. Enumerates sources, imports
   each, reads/validates metadata, checks SDK/app compatibility, and
   classifies: ``ready`` / ``disabled`` / ``pending_consent`` /
   ``incompatible`` / ``error`` / ``duplicate``. No plugin ``activate`` runs.
2. Consent UI (app.py) answers each ``pending_consent`` via
   ``record_consent`` — persisted so the user is asked exactly once.
3. ``activate_enabled()`` — GUI thread, before ``backend.start()``: builds a
   ``HostPluginContext`` per ready plugin and calls ``activate``. A raise
   flips the plugin to ``error`` and unwinds its partial registrations.
4. app.py materializes ``window_specs()`` / ``page_specs()``.
5. ``shutdown()`` on app quit (after the driver joined): ``deactivate`` each
   active plugin, then release host-owned network resources.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from nparseplus.config.paths import plugin_data_dir, plugins_dir
from nparseplus.config.settings import PluginEntry, Settings
from nparseplus.core.plugins.context import HostPluginContext, _OwnedNet
from nparseplus.core.plugins.discovery import PluginSource, discover_all
from nparseplus.core.plugins.storage import JsonPluginStorage
from nparseplus_sdk import SDK_VERSION as _SDK_VERSION
from nparseplus_sdk import NParsePlugin, PluginMeta, check_compat
from nparseplus_sdk.plugin import PluginSettingsPageSpec, PluginWindowSpec

if TYPE_CHECKING:
    from nparseplus.composition import Backend

logger = logging.getLogger(__name__)

PluginStatus = Literal[
    "ready",  # loaded + enabled; will activate
    "active",  # activate() succeeded
    "disabled",  # user-disabled (or consent declined)
    "pending_consent",  # never seen before; awaiting the first-load dialog
    "incompatible",  # SDK/app version handshake failed
    "error",  # import/create/activate raised (see .error)
    "duplicate",  # same meta.id as an earlier source
]


@dataclass
class LoadedPlugin:
    source: PluginSource
    status: PluginStatus
    meta: PluginMeta | None = None
    plugin: NParsePlugin | None = None
    error: str | None = None
    context: HostPluginContext | None = None
    window_specs: list[PluginWindowSpec] = field(default_factory=list)
    page_specs: list[PluginSettingsPageSpec] = field(default_factory=list)

    @property
    def display_name(self) -> str:
        return self.meta.name if self.meta is not None else self.source.name

    @property
    def plugin_id(self) -> str | None:
        return self.meta.id if self.meta is not None else None


class PluginHost:
    def __init__(
        self,
        settings: Settings,
        backend: Backend,
        app_version: str,
        request_save: Callable[[], None] | None = None,
        plugins_dir_override: Path | None = None,
    ) -> None:
        self._settings = settings
        self._backend = backend
        self._app_version = app_version
        self._request_save = request_save
        self._plugins_dir = plugins_dir_override or plugins_dir()
        self._owned_net = _OwnedNet(backend)
        self._loaded: list[LoadedPlugin] = []

    @property
    def plugins_dir(self) -> Path:
        return self._plugins_dir

    @property
    def sdk_version(self) -> str:
        return _SDK_VERSION

    # --- discovery / classification ---------------------------------------
    def discover_and_load(self) -> None:
        seen_ids: set[str] = set()
        for source in discover_all(self._plugins_dir):
            self._loaded.append(self._load_one(source, seen_ids))
        for loaded in self._loaded:
            if loaded.status != "ready":
                logger.info(
                    "plugin %s (%s): %s%s",
                    loaded.display_name,
                    loaded.source.location,
                    loaded.status,
                    f" — {loaded.error}" if loaded.error else "",
                )

    def _load_one(self, source: PluginSource, seen_ids: set[str]) -> LoadedPlugin:
        try:
            factory = source.load()
            plugin = factory()
        except Exception as exc:
            logger.exception("plugin %s failed to load", source.location)
            return LoadedPlugin(source=source, status="error", error=repr(exc))

        raw_meta = getattr(plugin, "meta", None)
        try:
            meta = PluginMeta.model_validate(raw_meta, from_attributes=True)
        except Exception as exc:
            return LoadedPlugin(source=source, status="error", error=f"invalid metadata: {exc}")

        if meta.id in seen_ids:
            return LoadedPlugin(
                source=source,
                status="duplicate",
                meta=meta,
                error=f"another plugin already claimed id {meta.id!r}",
            )
        seen_ids.add(meta.id)

        if not isinstance(plugin, NParsePlugin) and not callable(getattr(plugin, "activate", None)):
            return LoadedPlugin(
                source=source, status="error", meta=meta, error="plugin has no activate() method"
            )

        reason = check_compat(meta, sdk_version=_SDK_VERSION, app_version=self._app_version)
        if reason is not None:
            return LoadedPlugin(source=source, status="incompatible", meta=meta, error=reason)

        entry = self._settings.plugins.entries.get(meta.id)
        if entry is None or not entry.approved:
            return LoadedPlugin(source=source, status="pending_consent", meta=meta, plugin=plugin)
        if not entry.enabled:
            return LoadedPlugin(source=source, status="disabled", meta=meta, plugin=plugin)
        if entry.last_version != meta.version:
            entry.last_version = meta.version
            self._save()
        return LoadedPlugin(source=source, status="ready", meta=meta, plugin=plugin)

    # --- consent -----------------------------------------------------------
    def pending_consent(self) -> list[LoadedPlugin]:
        return [p for p in self._loaded if p.status == "pending_consent"]

    def record_consent(self, plugin_id: str, allowed: bool) -> None:
        """Persist the first-load answer and reclassify the plugin."""
        for loaded in self._loaded:
            if loaded.plugin_id != plugin_id or loaded.status != "pending_consent":
                continue
            assert loaded.meta is not None
            self._settings.plugins.entries[plugin_id] = PluginEntry(
                enabled=allowed, approved=True, last_version=loaded.meta.version
            )
            loaded.status = "ready" if allowed else "disabled"
            self._save()
            return

    def entry_for(self, plugin_id: str) -> PluginEntry | None:
        """The persisted consent/enable entry for a plugin id, if any."""
        return self._settings.plugins.entries.get(plugin_id)

    def set_enabled(self, plugin_id: str, enabled: bool) -> None:
        """Enable/disable a known plugin (takes effect next launch)."""
        entry = self._settings.plugins.entries.get(plugin_id)
        if entry is None:
            entry = PluginEntry(approved=True)
            self._settings.plugins.entries[plugin_id] = entry
        entry.enabled = enabled
        self._save()

    # --- activation ---------------------------------------------------------
    def activate_enabled(self) -> None:
        for loaded in self._loaded:
            if loaded.status != "ready" or loaded.plugin is None or loaded.meta is None:
                continue
            storage = JsonPluginStorage(plugin_data_dir(loaded.meta.id))
            ctx = HostPluginContext(
                loaded.meta, self._backend, self._app_version, storage, self._owned_net
            )
            try:
                loaded.plugin.activate(ctx)
            except Exception as exc:
                logger.exception("plugin %s activate() failed; unwinding", loaded.meta.id)
                ctx.unwind()
                loaded.status = "error"
                loaded.error = f"activate() raised: {exc!r}"
                continue
            loaded.context = ctx
            loaded.window_specs = list(ctx.window_specs)
            loaded.page_specs = list(ctx.page_specs)
            loaded.status = "active"
            logger.info("plugin %s v%s activated", loaded.meta.id, loaded.meta.version)

    # --- queries ------------------------------------------------------------
    def statuses(self) -> list[LoadedPlugin]:
        return list(self._loaded)

    def window_specs(self) -> list[tuple[LoadedPlugin, PluginWindowSpec]]:
        return [
            (loaded, spec)
            for loaded in self._loaded
            if loaded.status == "active"
            for spec in loaded.window_specs
        ]

    def page_specs(self) -> list[tuple[LoadedPlugin, PluginSettingsPageSpec]]:
        return [
            (loaded, spec)
            for loaded in self._loaded
            if loaded.status == "active"
            for spec in loaded.page_specs
        ]

    # --- shutdown -----------------------------------------------------------
    def shutdown(self) -> None:
        """Deactivate active plugins; call after the driver thread has joined."""
        for loaded in self._loaded:
            if loaded.status != "active" or loaded.plugin is None:
                continue
            try:
                loaded.plugin.deactivate()
            except Exception:
                logger.exception("plugin %s deactivate() raised", loaded.plugin_id)
        self._owned_net.close()

    def _save(self) -> None:
        if self._request_save is not None:
            self._request_save()
