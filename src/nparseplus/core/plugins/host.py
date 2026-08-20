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

Steps 3-5 are also reachable one plugin at a time, while the app runs:
``activate_one`` / ``deactivate_one`` (and ``adopt_installed`` for something
installed this session) are what ``set_enabled`` drives, so enabling or
disabling an add-on no longer costs a restart (#45). Live registration
changes are safe because every one of them is routed onto the driver thread
by ``LogDriver.submit_to_driver``; what is deliberately NOT live is
re-importing a plugin whose code changed in place, and the master
``plugins.enabled`` switch, which stays restart-only by design.
"""

from __future__ import annotations

import logging
import os
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
from nparseplus.core.plugins.telemetry import MetricsCollector, PluginStatsSnapshot
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
        # One collector for the run (#132). Built here rather than in
        # composition because it is plugin-scoped by construction: with
        # add-ons off this class is never imported, which is what keeps the
        # measurement off the no-plugin hot loop entirely.
        self._metrics = MetricsCollector(enabled=settings.plugins.telemetry)
        self._loaded: list[LoadedPlugin] = []
        # Latest update check, session-only. See cache_update_check.
        self._update_check: UpdateCheckResult | None = None
        # The two halves of "a plugin's surfaces follow the plugin", on the
        # ``TimersService.on_change`` pattern. Core never learns what a
        # window is: the build hook hands over the row (its ``window_specs``
        # and ``page_specs`` are what the UI needs), the teardown hook a bare
        # plugin id. Failures in a listener are logged, never propagated.
        #
        # Registered AFTER the startup sweep, so ``activate_enabled`` does
        # not build the same windows the caller is about to build itself.
        self.on_ui_build: list[Callable[[LoadedPlugin], None]] = []
        self.on_ui_teardown: list[Callable[[str], None]] = []

    @property
    def plugins_dir(self) -> Path:
        return self._plugins_dir

    @property
    def sdk_version(self) -> str:
        return _SDK_VERSION

    # --- performance telemetry (#132) ---------------------------------------
    @property
    def telemetry_enabled(self) -> bool:
        return self._metrics.enabled

    def set_telemetry(self, enabled: bool) -> None:
        """Turn per-plugin measurement on or off, live and for everything.

        Live because the gate is a flag the wrappers read, not a wrapper they
        are: re-registering every callback to change this would be a far
        bigger intervention than the thing being switched.
        """
        self._settings.plugins.telemetry = enabled
        self._metrics.set_enabled(enabled)
        self._save()

    def stats(self) -> dict[str, PluginStatsSnapshot]:
        """Snapshot every plugin's numbers — GUI-thread read, plain data."""
        return self._metrics.snapshots()

    def stats_for(self, plugin_id: str) -> PluginStatsSnapshot | None:
        metrics = self._metrics.get(plugin_id)
        return metrics.snapshot() if metrics is not None else None

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
        declared = meta.update_url
        if entry is None or entry.update_url == declared:
            return
        entry.update_url = declared
        self._save()

    # --- consent -----------------------------------------------------------
    def pending_consent(self) -> list[LoadedPlugin]:
        return [p for p in self._loaded if p.status == "pending_consent"]

    def record_consent(self, plugin_id: str, allowed: bool) -> LoadedPlugin | None:
        """Persist the first-load answer and reclassify the plugin.

        Answers a plugin awaiting consent — and also one already ``disabled``,
        because a plugin installed during this session is consented from the
        manager rather than from the startup sweep, and a user who declines,
        thinks better of it and installs again would otherwise face a row that
        cannot be re-approved without a restart. Nothing else is touched: a
        row that is ``active``, ``incompatible``, ``error`` or ``duplicate``
        has an answer already, or has a problem consent cannot fix.

        Reclassifies only — the plugin is not activated here, so the startup
        order (prompt every plugin, then ``activate_enabled``) is unchanged.
        ``set_enabled`` is the live path.

        The entry is *updated*, never replaced: ``record_install`` has already
        written this plugin's provenance (source URL, sha256, the registry
        that vouched for it) and replacing the entry would drop it, which is
        what makes the built-in registry's next update offer look like it came
        from a stranger.
        """
        for loaded in self._loaded:
            if loaded.plugin_id != plugin_id or loaded.meta is None:
                continue
            if loaded.status not in ("pending_consent", "disabled"):
                continue
            entry = self._settings.plugins.entries.get(plugin_id)
            if entry is None:
                entry = PluginEntry()
                self._settings.plugins.entries[plugin_id] = entry
            entry.enabled = allowed
            entry.approved = True
            entry.last_version = loaded.meta.version
            entry.update_url = loaded.meta.update_url
            loaded.status = "ready" if allowed else "disabled"
            self._save()
            return loaded
        return None

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
        entry.update_url = result.meta.update_url
        self._save()

    def forget(self, plugin_id: str) -> None:
        """Stop, drop and erase the persistent traces of an uninstalled plugin.

        Uninstalling only moves the code to the trash; the consent record and
        the plugin's private data would outlive it. That is a consent bypass:
        the next thing to claim id ``plugin_id`` — from any source — would
        load pre-approved and inherit the old plugin's storage. Both go with
        the code, so a re-install is treated as the stranger it is.

        Since #45 an uninstall can happen while the plugin is *running*, so
        this deactivates first — registrations, timer rows and windows all
        come out — and then drops the row entirely. Leaving it would list a
        plugin whose code is in the trash, and dropping it while it still had
        hooks in the bus would strand them with nothing left to unwind them.
        """
        self.deactivate_one(plugin_id)
        self._loaded = [loaded for loaded in self._loaded if loaded.plugin_id != plugin_id]
        if self._settings.plugins.entries.pop(plugin_id, None) is not None:
            self._save()
        self._metrics.forget(plugin_id)
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
            declared = loaded.meta.update_url if loaded.meta is not None else ""
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

    def set_enabled(self, plugin_id: str, enabled: bool) -> LoadedPlugin | None:
        """Enable/disable a known plugin, live — no restart (#45).

        Persists the answer and then makes it true now: enabling activates the
        plugin, disabling deactivates it, unwinds its registrations, drops its
        timer rows and asks the UI to destroy its windows. Returns the row the
        call acted on, or None when ``plugin_id`` is not installed; the caller
        reads ``.status`` for the outcome, since a plugin that raises on the
        way up lands in ``error`` rather than ``active``.

        Enabling never stands in for consent. A plugin whose entry is not
        ``approved`` stays where it is (``pending_consent``): the checkbox
        records the wish, and the first-load dialog — ``record_consent`` —
        is still what lets it run.

        What is NOT live, deliberately: the master ``plugins.enabled`` switch
        (restart-only by design — see ``pluginbootstrap``), and code that
        changed on disk. Only a fresh install imports; an update in place
        cannot be re-imported safely in-session, so it keeps its restart
        notice (stale ``<stem>.helper`` submodules survive a top-level
        re-import, and the old plugin's live objects keep the old globals).
        """
        entry = self._settings.plugins.entries.get(plugin_id)
        if entry is None:
            # No entry means consent was never given (or was forgotten by an
            # uninstall) — ticking a checkbox must not stand in for it.
            entry = PluginEntry(approved=False)
            self._settings.plugins.entries[plugin_id] = entry
        entry.enabled = enabled
        self._save()
        if not enabled:
            return self.deactivate_one(plugin_id)
        loaded = self._row_for(plugin_id)
        if loaded is None or not entry.approved:
            return loaded
        if loaded.status == "disabled":
            loaded.status = "ready"
        return self.activate_one(plugin_id)

    # --- activation ---------------------------------------------------------
    def activate_enabled(self) -> None:
        for loaded in self._loaded:
            self._activate(loaded)

    def activate_one(self, plugin_id: str) -> LoadedPlugin | None:
        """Activate one ``ready`` plugin now; returns its row (None: unknown).

        The loop body of ``activate_enabled``, addressable — and callable
        while the driver runs, which is the whole of #45's "a driver-thread-
        safe way to add registrations": the context registers subscriptions,
        parsers and ticks, and each of those is routed onto the driver thread
        by ``LogDriver.submit_to_driver`` (the bus already tolerates it).

        A row that is not ``ready`` is returned untouched, so activating an
        already-active plugin is a no-op rather than a second ``activate()``.
        A plugin that *failed* to activate is reachable again by disabling it
        first — ``deactivate_one`` retires an ``error`` row to ``disabled``,
        from which enabling retries it.
        Nothing here imports: every discovered plugin was imported and
        constructed by ``discover_and_load`` regardless of consent, so
        enabling one is only the call it never got.
        """
        loaded = self._row_for(plugin_id)
        if loaded is not None:
            self._activate(loaded)
        return loaded

    def deactivate_one(self, plugin_id: str) -> LoadedPlugin | None:
        """Deactivate one plugin now; returns its row (None: unknown).

        ``shutdown``'s loop body plus the three things it has no reason to do
        at process exit, and which a live disable must: put the row back to
        ``disabled``, drop its context, and take down what it built — its
        timer rows here, its windows and settings pages through
        ``on_ui_teardown`` (Qt lives on the other side of that callback).

        Isolation runs the other way from ``activate_one``: a plugin that
        raises on the way down does not get to keep its registrations, so the
        unwind and the teardown happen either way and nothing propagates.
        """
        loaded = self._row_for(plugin_id)
        if loaded is None:
            return None
        self._teardown(loaded, retire=True)
        return loaded

    def adopt_installed(self, path: Path) -> LoadedPlugin | None:
        """Classify a plugin installed during this session; None if not one.

        The missing half of a live install: the sweep that classifies plugins
        runs once at startup, so an add-on installed afterwards is invisible
        to the host until the next launch. Same ``_load_one`` as that sweep,
        against the ids already claimed, so a new install that collides lands
        in ``duplicate`` exactly as it would have.

        A path already loaded returns its existing row, unchanged and
        un-re-imported. That is the import-once-per-session rule, not an
        optimisation: replacing a plugin's code in place and re-importing it
        leaves the old module's objects live and its submodules stale, so an
        update in place is the one plugin operation that still needs a
        restart.

        Consent is untouched: a brand-new plugin arrives ``pending_consent``,
        which is what ``record_install`` set up by writing an unapproved
        entry.
        """
        from nparseplus.core.plugins.discovery import source_for_path

        target = _normalized(path)
        for loaded in self._loaded:
            if loaded.source.origin != "dir":
                continue
            if _normalized(Path(loaded.source.location)) == target:
                return loaded
        source = source_for_path(path)
        if source is None:
            return None
        loaded = self._load_one(source, self._claimed_ids())
        self._loaded.append(loaded)
        logger.info(
            "plugin %s (%s) adopted mid-session: %s",
            loaded.display_name,
            loaded.source.location,
            loaded.status,
        )
        return loaded

    # --- lifecycle internals -------------------------------------------------
    def _row_for(self, plugin_id: str) -> LoadedPlugin | None:
        """The row that owns ``plugin_id`` — never one shadowed as a duplicate."""
        return next(
            (
                loaded
                for loaded in self._loaded
                if loaded.plugin_id == plugin_id and loaded.status != "duplicate"
            ),
            None,
        )

    def _claimed_ids(self) -> set[str]:
        """Ids already spoken for, as ``discover_and_load``'s sweep sees them."""
        return {
            loaded.plugin_id
            for loaded in self._loaded
            if loaded.plugin_id is not None and loaded.status != "duplicate"
        }

    def _activate(self, loaded: LoadedPlugin) -> None:
        if loaded.status != "ready" or loaded.plugin is None or loaded.meta is None:
            return
        storage = JsonPluginStorage(self._plugin_data_dir(loaded.meta.id))
        # for_plugin also zeroes an earlier run's numbers, which is what makes
        # a disable/re-enable read as the fresh run it is.
        ctx = HostPluginContext(
            loaded.meta,
            self._backend,
            self._app_version,
            storage,
            self._owned_net,
            metrics=self._metrics.for_plugin(loaded.meta.id),
        )
        try:
            loaded.plugin.activate(ctx)
        except Exception as exc:
            logger.exception("plugin %s activate() failed; unwinding", loaded.meta.id)
            ctx.unwind()
            loaded.status = "error"
            loaded.error = f"activate() raised: {exc!r}"
            return
        loaded.context = ctx
        loaded.window_specs = list(ctx.window_specs)
        loaded.page_specs = list(ctx.page_specs)
        loaded.status = "active"
        logger.info("plugin %s v%s activated", loaded.meta.id, loaded.meta.version)
        self._build_ui(loaded)

    def _teardown(self, loaded: LoadedPlugin, *, retire: bool) -> None:
        """Deactivate + unwind; ``retire`` also puts the row back to disabled.

        ``retire`` is what separates a live disable from process exit: at exit
        there is nobody left to show a status to, no window to destroy and no
        Timers window to clean up, so ``shutdown`` skips all of it.
        """
        had_ui = bool(loaded.window_specs or loaded.page_specs)
        if loaded.status == "active" and loaded.plugin is not None:
            try:
                loaded.plugin.deactivate()
            except Exception:
                logger.exception("plugin %s deactivate() raised", loaded.plugin_id)
        # Unwind after deactivate, so the plugin still has its registrations
        # while it shuts down — and after the raise above, because a plugin
        # that failed to stop is precisely one whose hooks must come out.
        if loaded.context is not None:
            loaded.context.unwind()
        loaded.window_specs.clear()
        loaded.page_specs.clear()
        if not retire:
            return
        plugin_id = loaded.plugin_id
        if plugin_id is not None:
            self._drop_timer_rows(plugin_id)
            self._teardown_ui(plugin_id, had_ui)
        loaded.context = None
        # ``error`` retires too, which is what makes a failed activation
        # retryable: the plugin object is still imported and constructed, so
        # unticking and re-ticking the box puts it back to ``ready`` and runs
        # activate() again. Without it a plugin that raised once was stuck in
        # ``error`` for the session with no way back but a restart — and the
        # fix for a transient failure (a file it wanted, a login it needed) is
        # usually just to try again.
        if loaded.status in ("active", "ready", "error"):
            loaded.status = "disabled"

    def _drop_timer_rows(self, plugin_id: str) -> None:
        """Take a disabled plugin's countdowns off the Timers window.

        Through the driver's command inbox because ``TimersService`` belongs
        to the driver thread and this is called from the GUI thread; with no
        driver running (tests, and the shutdown path) it applies inline.
        """
        timers = self._backend.timers

        def drop() -> None:
            timers.remove_owner(plugin_id)

        self._backend.driver.submit_to_driver(drop, label=f"drop {plugin_id} timer rows")

    def _build_ui(self, loaded: LoadedPlugin) -> None:
        """Ask whoever owns the UI to materialize what this plugin declared.

        Inert during the startup sweep — nothing is listening yet, because
        the app registers here only after ``activate_enabled`` returns and it
        has built that first batch itself.
        """
        for callback in list(self.on_ui_build):
            try:
                callback(loaded)
            except Exception:
                logger.exception("plugin %s UI build raised", loaded.plugin_id)

    def _teardown_ui(self, plugin_id: str, had_ui: bool) -> None:
        """Ask whoever built this plugin's surfaces to destroy them.

        With nobody listening, a plugin that contributed a window or a
        settings page is switched off underneath surfaces that stay on
        screen. The disable itself still happens — refusing it would leave a
        misbehaving add-on running, which is the reason people reach for the
        checkbox — so the mismatch is logged rather than silently accepted.
        Wiring the listener is the Qt layer's half of #45.
        """
        if had_ui and not self.on_ui_teardown:
            logger.warning(
                "plugin %s was disabled but nothing is registered to destroy its "
                "windows/settings pages; they stay until the app restarts",
                plugin_id,
            )
        for callback in list(self.on_ui_teardown):
            try:
                callback(plugin_id)
            except Exception:
                logger.exception("plugin %s UI teardown raised", plugin_id)

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
        """Deactivate active plugins; call after the driver thread has joined.

        The same teardown ``deactivate_one`` runs, minus the retirement: at
        process exit there is no row to re-label, no window to destroy and no
        Timers window left to tidy. The shared net resources close last, and
        only here — they belong to every plugin at once, so disabling one
        must not take them from the rest.
        """
        for loaded in self._loaded:
            if loaded.status != "active":
                continue
            self._teardown(loaded, retire=False)
        self._owned_net.close()

    def _save(self) -> None:
        if self._request_save is not None:
            self._request_save()


def _normalized(path: Path) -> str:
    """Path identity for "is this already loaded", Windows/macOS case included."""
    return os.path.normcase(os.path.abspath(path))
