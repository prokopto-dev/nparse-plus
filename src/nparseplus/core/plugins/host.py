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
from typing import TYPE_CHECKING, Literal, cast

from nparseplus.config.paths import plugin_data_dir, plugins_dir
from nparseplus.config.settings import PluginEntry, Settings
from nparseplus.core.plugins.context import HostPluginContext, _OwnedNet
from nparseplus.core.plugins.discovery import PluginSource, discover_all
from nparseplus.core.plugins.install import InstallResult, trash_plugin_data
from nparseplus.core.plugins.storage import JsonPluginStorage
from nparseplus_sdk import SDK_VERSION as _SDK_VERSION
from nparseplus_sdk import NParsePlugin, PluginMeta, check_compat
from nparseplus_sdk.plugin import PluginSettingsPageSpec, PluginWindowSpec

if TYPE_CHECKING:
    from nparseplus.composition import Backend
    from nparseplus.core.plugins.registry import ResolvedRegistry
    from nparseplus.core.plugins.updatecheck import InstalledPlugin, UpdateCheckResult

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

    @property
    def tick_dropped(self) -> str | None:
        """Why the driver evicted this plugin's tick, if it did.

        The driver drops a plugin tick that repeatedly overruns its budget
        (core/driver.py). The plugin stays active — its parsers, handlers and
        windows still work — so the fact needs saying somewhere the user
        looks: the manager page reads this.
        """
        return self.context.tick_dropped if self.context is not None else None


class PluginHost:
    def __init__(
        self,
        settings: Settings,
        backend: Backend,
        app_version: str,
        request_save: Callable[[], None] | None = None,
        plugins_dir_override: Path | None = None,
        plugin_data_dir_override: Callable[[str], Path] | None = None,
    ) -> None:
        self._settings = settings
        self._backend = backend
        self._app_version = app_version
        self._request_save = request_save
        self._plugins_dir = plugins_dir_override or plugins_dir()
        self._plugin_data_dir = plugin_data_dir_override or plugin_data_dir
        self._owned_net = _OwnedNet(backend)
        self._loaded: list[LoadedPlugin] = []
        # Latest update check, session-only. See cache_update_check.
        self._update_check: UpdateCheckResult | None = None

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
            plugin = cast("NParsePlugin", factory())
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

        # Before any classification returns: every state below — incompatible,
        # declined, disabled — is one where an update might be the fix, so the
        # feed has to be recorded even when the plugin never runs.
        self._mirror_update_url(meta)

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

    def _mirror_update_url(self, meta: PluginMeta) -> None:
        """Cache the plugin's declared update feed on its settings entry.

        The live ``meta`` is the truth while a plugin is loaded, but the
        checker also has to reach plugins that are not — so the last observed
        value is kept on the entry. Clearing it when a plugin drops the field
        matters as much as recording it: an author who removes their feed
        should stop being polled, not be polled forever from a stale cache.
        """
        entry = self._settings.plugins.entries.get(meta.id)
        declared = getattr(meta, "update_url", "")
        if entry is None or entry.update_url == declared:
            return
        entry.update_url = declared
        self._save()

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
                enabled=allowed,
                approved=True,
                last_version=loaded.meta.version,
                update_url=getattr(loaded.meta, "update_url", ""),
            )
            loaded.status = "ready" if allowed else "disabled"
            self._save()
            return

    def entry_for(self, plugin_id: str) -> PluginEntry | None:
        """The persisted consent/enable entry for a plugin id, if any."""
        return self._settings.plugins.entries.get(plugin_id)

    # --- registries ---------------------------------------------------------
    def registries(self) -> list[ResolvedRegistry]:
        """Built-in default first, then the user's, enabled or not."""
        from nparseplus.core.plugins.registry import resolve_registries

        return resolve_registries(self._settings.plugins)

    def enabled_registries(self) -> list[ResolvedRegistry]:
        return [registry for registry in self.registries() if registry.enabled]

    def add_registry(self, url: str, name: str = "") -> str | None:
        """Add a user registry; returns an error message, or None on success."""
        from nparseplus.config.settings import RegistrySource, normalize_registry_url
        from nparseplus.core.plugins.registry import DEFAULT_REGISTRY_URL

        try:
            normalized = normalize_registry_url(url)
        except ValueError as exc:
            return str(exc)
        if normalized.lower() == DEFAULT_REGISTRY_URL.lower():
            return "that is the built-in registry — it is already in the list"
        if any(
            source.url.lower() == normalized.lower() for source in self._settings.plugins.registries
        ):
            return "that registry is already in the list"
        self._settings.plugins.registries.append(RegistrySource(url=normalized, name=name.strip()))
        self._save()
        return None

    def remove_registry(self, url: str) -> bool:
        """Remove a user registry. The built-in default is never removable."""
        from nparseplus.core.plugins.registry import DEFAULT_REGISTRY_URL

        if url.lower() == DEFAULT_REGISTRY_URL.lower():
            return False
        remaining = [
            source
            for source in self._settings.plugins.registries
            if source.url.lower() != url.lower()
        ]
        if len(remaining) == len(self._settings.plugins.registries):
            return False
        self._settings.plugins.registries = remaining
        self._save()
        return True

    def set_registry_enabled(self, url: str, enabled: bool) -> None:
        """Tick/untick a registry. The default can be unticked, not deleted."""
        from nparseplus.core.plugins.registry import DEFAULT_REGISTRY_URL

        if url.lower() == DEFAULT_REGISTRY_URL.lower():
            self._settings.plugins.default_registry_enabled = enabled
            self._save()
            return
        for source in self._settings.plugins.registries:
            if source.url.lower() == url.lower():
                source.enabled = enabled
                self._save()
                return

    def record_install(self, result: InstallResult, *, registry_url: str = "") -> None:
        """Persist provenance for a successful install (URL/registry/file).

        Consent semantics are unchanged: a brand-new plugin gets an
        unapproved entry, so the first-load dialog still runs next launch;
        an existing entry keeps its enabled/approved answers.

        ``registry_url`` is the registry that vouched for the artifact, empty
        for a plain URL or file install.
        """
        if not result.ok or result.meta is None:
            return
        entry = self._settings.plugins.entries.get(result.meta.id)
        if entry is None:
            entry = PluginEntry(approved=False)
            self._settings.plugins.entries[result.meta.id] = entry
        entry.last_version = result.meta.version
        entry.source_url = result.source_url or ""
        entry.sha256 = result.sha256 or ""
        entry.registry_url = registry_url
        entry.update_url = getattr(result.meta, "update_url", "")
        self._save()

    def forget(self, plugin_id: str) -> None:
        """Erase the persistent traces of an uninstalled plugin.

        Uninstalling only moves the code to the trash; the consent record and
        the plugin's private data would outlive it. That is a consent bypass:
        the next thing to claim id ``plugin_id`` — from any source — would
        load pre-approved and inherit the old plugin's storage. Both go with
        the code, so a re-install is treated as the stranger it is.
        """
        if self._settings.plugins.entries.pop(plugin_id, None) is not None:
            self._save()
        error = trash_plugin_data(self._plugin_data_dir(plugin_id), self._plugins_dir)
        if error is not None:
            logger.warning("plugin %s data not moved aside: %s", plugin_id, error)

    # --- update checks -------------------------------------------------------
    def set_update_check(self, enabled: bool) -> None:
        """Tick/untick the post-launch poll for plugin updates."""
        self._settings.plugins.update_check = enabled
        self._save()

    @property
    def update_check_enabled(self) -> bool:
        return self._settings.plugins.update_check

    def cache_update_check(self, result: UpdateCheckResult | None) -> None:
        """Store the latest check for whoever asks next.

        Written from the post-launch worker thread and read on the GUI thread.
        That is safe because it is one attribute assignment of an immutable
        result — there is no partially-visible state to observe — and nothing
        reacts to the write at the moment it lands. Session-only, deliberately:
        a persisted "update available" would outlive the update being taken and
        greet the user again after the restart that applied it.
        """
        self._update_check = result

    def cached_update_check(self) -> UpdateCheckResult | None:
        return self._update_check

    def installed_for_update_check(self) -> list[InstalledPlugin]:
        """Every installed plugin, as the update checker needs to see it.

        Covers plugins that never loaded — declined, disabled, incompatible —
        because those are exactly the states an update might resolve, and a
        row that cannot say "update available" is a dead end for the user.

        Feeds are the exception: ``update_url`` is only carried through for
        plugins the user approved and left enabled. A declined plugin must not
        be able to make the app call home to an author-chosen URL on every
        launch — that is a request the user effectively said no to.
        """
        from nparseplus.core.plugins.updatecheck import InstalledPlugin

        installed: list[InstalledPlugin] = []
        seen: set[str] = set()
        for loaded in self._loaded:
            plugin_id = loaded.plugin_id
            if plugin_id is None or plugin_id in seen:
                continue
            seen.add(plugin_id)
            entry = self._settings.plugins.entries.get(plugin_id)
            version = loaded.meta.version if loaded.meta is not None else ""
            if not version and entry is not None:
                version = entry.last_version
            allowed = entry is not None and entry.approved and entry.enabled
            declared = getattr(loaded.meta, "update_url", "") if loaded.meta is not None else ""
            if not declared and entry is not None:
                declared = entry.update_url
            installed.append(
                InstalledPlugin(
                    plugin_id=plugin_id,
                    version=version,
                    registry_url=entry.registry_url if entry is not None else "",
                    # Entry-point plugins live in site-packages; pip owns them,
                    # so there is nothing here that may be replaced in place.
                    installed_path=(
                        Path(loaded.source.location) if loaded.source.origin == "dir" else None
                    ),
                    update_url=declared if allowed else "",
                )
            )
        return installed

    def set_enabled(self, plugin_id: str, enabled: bool) -> None:
        """Enable/disable a known plugin (takes effect next launch)."""
        entry = self._settings.plugins.entries.get(plugin_id)
        if entry is None:
            # No entry means consent was never given (or was forgotten by an
            # uninstall) — ticking a checkbox must not stand in for it.
            entry = PluginEntry(approved=False)
            self._settings.plugins.entries[plugin_id] = entry
        entry.enabled = enabled
        self._save()

    # --- activation ---------------------------------------------------------
    def activate_enabled(self) -> None:
        for loaded in self._loaded:
            if loaded.status != "ready" or loaded.plugin is None or loaded.meta is None:
                continue
            storage = JsonPluginStorage(self._plugin_data_dir(loaded.meta.id))
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
            # Unwind after deactivate, so the plugin still has its
            # registrations while it shuts down. Harmless at process exit,
            # but leaving the bus/tick/parser hooks behind is exactly what
            # would break hot enable/disable.
            if loaded.context is not None:
                loaded.context.unwind()
            loaded.window_specs.clear()
            loaded.page_specs.clear()
        self._owned_net.close()

    def _save(self) -> None:
        if self._request_save is not None:
            self._request_save()
