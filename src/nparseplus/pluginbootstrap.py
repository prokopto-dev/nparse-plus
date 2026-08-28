"""Plugin startup, kept out of ``app.py`` on purpose.

Add-ons are opt-in (``settings.plugins.enabled``, default off). Honouring
that means more than hiding a page: when plugins are off, none of the plugin
machinery may be *imported* — the host pulls in the SDK, the installer, the
registry client, httpx and zipfile, and the manager page builds a table
nobody asked for. Concentrating every plugin import behind the two functions
here leaves ``create_app`` with exactly two gated import sites, which
``tests/core/plugins/test_master_toggle.py`` checks structurally.

Both functions are called from ``create_app`` and nowhere else:
``start_plugins`` before Qt exists (discovery is Qt-free and must finish
before the driver thread starts, so plugin subscriptions, parsers and ticks
are registered race-free), ``build_plugin_ui`` after the windows exist.
"""

from __future__ import annotations

import logging
import re
import threading
import weakref
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path

    from nparseplus.composition import Backend
    from nparseplus.config.settings import Settings
    from nparseplus.core.plugins.host import LoadedPlugin, PluginHost
    from nparseplus.ui.qtbridge import QtEventBridge

logger = logging.getLogger(__name__)

# Deliberately a local constant, not an import of helpers/application.py's
# UPDATE_CHECK_DELAY_MS: that module reads nparse.config.json from the CWD at
# import time and drags in Qt. Staggered past the app's own 10 s check so the
# two do not fire together on a cold start.
PLUGIN_UPDATE_CHECK_DELAY_MS = 12_000


@dataclass
class _PluginSurfaces:
    """Everything one plugin currently has on screen.

    Recorded as it is built because the teardown hook is handed a bare plugin
    id — by then the host has already cleared the specs it was built from,
    and a widget cannot be asked which add-on made it.
    """

    window_keys: list[str] = field(default_factory=list)  # "plugin.<id>.<key>"
    widgets: list[Any] = field(default_factory=list)
    tray_labels: list[str] = field(default_factory=list)
    command_keys: list[str] = field(default_factory=list)
    page_specs: list[Any] = field(default_factory=list)
    # Event-overlay regions (#155): keys are namespaced like window keys, and
    # the widgets are kept separately from ``widgets`` because they are torn
    # down through the overlay rather than through the layout manager.
    region_keys: list[str] = field(default_factory=list)  # "plugin.<id>.<key>"
    region_widgets: list[Any] = field(default_factory=list)


@dataclass
class PluginUi:
    """What the plugin subsystem contributes to the assembled UI.

    Empty in every field when plugins are off, so ``create_app`` can splat
    these unconditionally instead of branching at each use.

    It is also what keeps contributing afterwards: ``attach_live`` subscribes
    it to the host's build/teardown hooks so a plugin enabled or disabled
    mid-session gains or loses exactly these surfaces (#45).
    """

    windows_by_key: dict[str, Any] = field(default_factory=dict)  # "plugin.<id>.<key>"
    # Event-overlay regions by namespaced key. Not merged into any window
    # table: a region has no tray entry, no chat toggle and no Settings >
    # Windows row — it is not a window, it lives INSIDE one.
    regions_by_key: dict[str, Any] = field(default_factory=dict)
    command_handles: dict[str, Any] = field(default_factory=dict)  # chat toggle_<name>
    # Tray label -> widget. Provisional until ``attach_live`` claims real
    # labels against the tray itself — at launch the tray does not exist yet,
    # so the only collisions ``build_plugin_ui`` can see are between plugins.
    tray: dict[str, Any] = field(default_factory=dict)
    extra_pages: list[Any] = field(default_factory=list)
    # Settings > Windows grid rows: (label, window key, widget). Only windows
    # built on the overlay recipe appear here — see build_plugin_ui.
    window_rows: list[tuple[str, str, Any]] = field(default_factory=list)
    # Per-plugin provenance for the live path; keyed by plugin id.
    surfaces: dict[str, _PluginSurfaces] = field(default_factory=dict)

    def attach_live(
        self,
        *,
        plugin_host: PluginHost,
        settings: Settings,
        save: Callable[[], None],
        bridge: QtEventBridge,
        window_handles: dict[str, Any],
        settings_window: Any,
        layouts: Any,
        legacy_app: Any,
        chrome_surfaces: list[Any],
        apply_appearance: Callable[[], None],
        event_overlay: Any = None,
    ) -> None:
        """Make plugin surfaces follow the plugin, for the rest of the session.

        Called from ``create_app`` once the settings window, the layout
        manager and the tray exist — which is *after* ``build_plugin_ui``,
        because those three are built from what it returns. That ordering is
        also why the hooks are subscribed here and not inside the host:
        subscribing any earlier would make ``activate_enabled`` build the
        startup windows a second time.

        Everything it needs is a live collection someone else owns, so the
        plugin layer never holds a private copy that can drift: the chat
        command table and the tray dict are mutated in place (the tray menu
        re-reads its dict every time it opens), the layout manager and the
        settings window grew add/remove methods, ``chrome_surfaces`` is the
        list a skin change sweeps, and ``event_overlay`` is the overlay whose
        region registry a contributed region joins and leaves.
        """
        live = _LivePluginUi(
            ui=self,
            plugin_host=plugin_host,
            settings=settings,
            save=save,
            bridge=bridge,
            window_handles=window_handles,
            settings_window=settings_window,
            layouts=layouts,
            legacy_app=legacy_app,
            chrome_surfaces=chrome_surfaces,
            apply_appearance=apply_appearance,
            event_overlay=event_overlay,
        )
        # The windows built at launch are in the tray dict provisionally; now
        # that the tray exists, claim real labels for them through exactly the
        # path a live enable uses.
        for plugin_id in list(self.surfaces):
            live.claim_tray_labels(plugin_id)
        # Regions built at launch join the skin sweep here rather than in
        # ``build_plugin_ui``: they are not windows, so ``create_app`` never
        # sees them in ``layout_windows``, which is the list it fills
        # ``chrome_surfaces`` from. Each is already dressed from the CURRENT
        # skin by its own constructor; what it needs is to be swept on the
        # NEXT change.
        chrome_surfaces.extend(
            widget for widget in self.regions_by_key.values() if widget not in chrome_surfaces
        )
        plugin_host.on_ui_build.append(live.materialize)
        plugin_host.on_ui_teardown.append(live.retire)


def start_plugins(
    settings: Settings,
    backend: Backend,
    app_version: str,
    request_save: Callable[[], None],
    plugins_dir_override: Path | None = None,
) -> PluginHost | None:
    """Discover and classify installed plugins; None if the sweep failed."""
    from nparseplus.core.plugins.host import PluginHost

    host = PluginHost(
        settings,
        backend,
        app_version,
        request_save=request_save,
        plugins_dir_override=plugins_dir_override,
    )
    try:
        host.discover_and_load()
    except Exception:
        # Per-plugin failures are already isolated inside discover_and_load;
        # this guards the sweep itself. A third-party add-on must never be
        # able to stop the app from starting.
        logger.exception("plugin discovery failed; continuing without plugins")
        return None
    return host


def build_plugin_ui(
    plugin_host: PluginHost,
    settings: Settings,
    app_version: str,
    save: Callable[[], None],
    bridge: QtEventBridge,
    window_handles: dict[str, Any],
    event_overlay: Any = None,
) -> PluginUi:
    """Run consent, activate, and materialize each plugin's Qt contributions.

    ``event_overlay`` is optional only so an embedder (and a test that builds
    no overlay) can leave it out; without one, a plugin's declared overlay
    regions are logged and dropped rather than silently forgotten.
    """
    from nparseplus.ui.pluginconsent import run_consent_prompts
    from nparseplus.ui.pluginmanager import plugin_manager_page_spec

    ui = PluginUi()
    ui.extra_pages.append(plugin_manager_page_spec(plugin_host, app_version))
    run_consent_prompts(plugin_host)
    plugin_host.activate_enabled()

    for loaded, spec, widget in _materialize_plugin_windows(plugin_host, settings, save, bridge):
        _register_window(ui, loaded, spec, widget, window_handles)

    for loaded in _plugins_with_regions(plugin_host):
        for spec, widget in _build_plugin_regions(loaded, settings, save, bridge, event_overlay):
            if _register_region(ui, loaded, spec, widget, event_overlay) is None:
                # Refused (a duplicate key): nothing holds it, and nothing ever
                # will — the same disposal the live path does.
                _discard_region_widget(widget)

    for loaded, spec in plugin_host.page_specs():
        ui.extra_pages.append(spec)
        if loaded.plugin_id is not None:
            ui.surfaces.setdefault(loaded.plugin_id, _PluginSurfaces()).page_specs.append(spec)
    if settings.plugins.update_check:
        schedule_update_check(plugin_host, app_version)
    return ui


def _register_window(
    ui: PluginUi,
    loaded: LoadedPlugin,
    spec: Any,
    widget: Any,
    window_handles: dict[str, Any],
) -> tuple[str, str] | None:
    """Record one built window in every collection that has to know about it.

    Shared by the startup sweep and the live path so a plugin enabled from
    the settings window joins exactly the same tables — nothing about a
    window built at launch is special. Returns ``(window key, tray label)``,
    or None if the window was refused as a duplicate.
    """
    assert loaded.meta is not None
    window_key = f"plugin.{loaded.meta.id}.{spec.key}"
    if window_key in ui.windows_by_key:
        # add_window() does not enforce unique spec.key, so a plugin can
        # declare the same one twice. Two widgets sharing one window key
        # would share one WindowState (and one Settings > Windows row) —
        # keep the first and say so, rather than let the second win the
        # dict while both draw.
        logger.warning(
            "plugin %s declared window key %r twice; the later window is ignored",
            loaded.meta.id,
            spec.key,
        )
        return None
    surfaces = ui.surfaces.setdefault(loaded.meta.id, _PluginSurfaces())
    ui.windows_by_key[window_key] = widget
    surfaces.window_keys.append(window_key)
    surfaces.widgets.append(widget)
    command_key = _plugin_command_key(loaded, spec.key, spec.command_key)
    if command_key in window_handles or command_key in ui.command_handles:
        logger.warning(
            "plugin %s window command %r collides; chat toggle skipped",
            loaded.meta.id,
            command_key,
        )
    else:
        ui.command_handles[command_key] = widget
        surfaces.command_keys.append(command_key)
    label = spec.title
    if label in ui.tray:
        label = f"{spec.title} ({loaded.meta.id})"
    ui.tray[label] = widget
    surfaces.tray_labels.append(label)
    _add_window_row(ui, loaded, spec, widget, window_key)
    return window_key, label


def _register_region(
    ui: PluginUi,
    loaded: LoadedPlugin,
    spec: Any,
    widget: Any,
    event_overlay: Any,
) -> str | None:
    """Put one built region on the overlay and record it. Returns its key.

    Shared by the startup sweep and the live path, exactly like
    ``_register_window`` — nothing about a region built at launch is special.
    Returns None when the region was refused, in which case the caller owns
    the widget it built.

    A region is deliberately registered NOWHERE else: no tray entry, no chat
    toggle, no Settings > Windows row. Those all belong to a top-level window,
    and a region is not one — it lives inside the event overlay and is placed
    from the overlay's own position mode.
    """
    assert loaded.meta is not None
    region_key = f"plugin.{loaded.meta.id}.{spec.key}"
    if region_key in ui.regions_by_key:
        # add_overlay_region() does not enforce a unique spec.key, so a plugin
        # can declare the same one twice. Two widgets sharing one region key
        # would share one persisted placement, and the overlay's registry is
        # keyed on it — the second would simply displace the first, leaving a
        # widget nothing can ever remove. Keep the first and say so.
        logger.warning(
            "plugin %s declared overlay region key %r twice; the later region is ignored",
            loaded.meta.id,
            spec.key,
        )
        return None
    placed = False
    with _isolated(f"plugin {loaded.meta.id} overlay region {spec.key!r}"):
        placed = bool(
            event_overlay.add_region(
                region_key,
                widget,
                title=spec.title or spec.key,
                has_content=_guarded_has_content(spec, loaded.meta.id),
                default=_region_default(spec, loaded.meta.id),
                default_width=_region_default_width(spec, loaded.meta.id),
                preview=_region_preview(widget, loaded.meta.id, spec.key),
            )
        )
    if not placed:
        # The overlay refuses a key it holds a BUILT-IN under, which is the
        # one case a plugin cannot talk its way out of.
        logger.warning(
            "plugin %s overlay region %r was refused by the overlay", loaded.meta.id, spec.key
        )
        # ROLL BACK, because "refused" and "left no trace" are not the same
        # thing. ``add_region`` registers the record and mints its chip BEFORE
        # it places the host, so a failure during layout leaves a record whose
        # host the caller is about to delete — and every later visibility pass
        # (i.e. every overlay event) then raises out of ``_region_size``. The
        # overlay stops working, permanently, for one bad add-on.
        with _isolated(f"plugin {loaded.meta.id} overlay region {spec.key!r} rollback"):
            event_overlay.remove_region(region_key)
        return None
    surfaces = ui.surfaces.setdefault(loaded.meta.id, _PluginSurfaces())
    ui.regions_by_key[region_key] = widget
    surfaces.region_keys.append(region_key)
    surfaces.region_widgets.append(widget)
    return region_key


def _region_default_width(spec: Any, plugin_id: str) -> Callable[[], int] | None:
    """``spec.default_width`` as a callable, or None to use the overlay default.

    The one declared size that does NOT go through ``OverlayRegion``'s pydantic
    validation, because the overlay wants a callable rather than a stored
    number — so it is the one a plugin can put anything in. Unvalidated, a
    ``default_width="wide"`` reached ``max(MIN_REGION_WIDTH, host_w)`` inside
    the overlay's layout pass and raised there, i.e. *after* the region had
    already been registered.
    """
    width = spec.default_width
    if width is None:
        return None
    if isinstance(width, bool) or not isinstance(width, int) or width <= 0:
        logger.warning(
            "plugin %s overlay region %r declared default_width=%r, which is not a positive "
            "int; using the overlay default",
            plugin_id,
            spec.key,
            width,
        )
        return None
    return lambda: width


def _region_default(spec: Any, plugin_id: str) -> Any:
    """The placement a contributed region starts at, or the plain default.

    ``OverlayRegion``'s own validator repairs an unusable size rather than
    raising, but ``default_anchor`` is a Literal and a plugin can pass
    anything — so a bad one costs that region its declared placement, not its
    place on the overlay.
    """
    from nparseplus.config.settings import OverlayRegion

    try:
        return OverlayRegion(
            anchor=spec.default_anchor,
            dx=spec.default_dx,
            dy=spec.default_dy,
            height=spec.default_height,
        )
    except Exception:
        logger.exception(
            "plugin %s overlay region %r declared an unusable default placement; "
            "using the overlay default",
            plugin_id,
            spec.key,
        )
        return OverlayRegion()


def _guarded_has_content(spec: Any, plugin_id: str) -> Callable[[], bool]:
    """``spec.has_content``, unable to break or tax the visibility pass.

    Asked on every visibility pass — which is every overlay event, on the GUI
    thread — so the first exception RETIRES the predicate outright: it is
    logged once and the region answers False from then on, without the
    predicate being called again. Suppressing only the log line would leave a
    permanently broken (or simply expensive) predicate running on every
    overlay event for the rest of the session, which is the cost this guard
    exists to avoid and not what "treated as empty for the rest of the
    session" says.

    A region that cannot say whether it has anything is not a reason to keep
    an always-on-top window over the game, so False is the safe answer — and
    it is one the plugin can recover from by being disabled and re-enabled,
    which builds a fresh guard.
    """
    retired = False

    def call() -> bool:
        nonlocal retired
        if retired:
            return False
        try:
            return bool(spec.has_content())
        except Exception:
            retired = True
            logger.exception(
                "plugin %s overlay region %r has_content() raised; it will not be asked "
                "again this session and the region is treated as empty",
                plugin_id,
                spec.key,
            )
            return False

    return call


def _region_preview(widget: Any, plugin_id: str, key: str) -> Callable[[], list[Any]] | None:
    """The region's position-mode sample content, guarded.

    None for a widget with no ``sample()`` — the overlay reads that as "shows
    nothing while positioning", which is already how a region with no preview
    factory behaves.
    """
    from PySide6.QtWidgets import QWidget

    sample = getattr(widget, "sample", None)
    if not callable(sample):
        return None

    def build() -> list[Any]:
        # EVERY step below is a call into plugin code, and all of them run from
        # ``_populate_preview`` during ``set_edit_mode(True)``. An escape here
        # does not cost this one region its preview — it stops POSITION MODE
        # OPENING AT ALL, for every region and every built-in.
        try:
            made = sample()
        except Exception:
            logger.exception("plugin %s overlay region %r sample() raised", plugin_id, key)
            return []
        if made is None:
            return []
        if isinstance(made, str | bytes):
            # Iterable, but never what was meant, and reporting it as a bad
            # sequence of widgets is more useful than 40 discarded characters.
            made = None
        try:
            iterator = iter(made)  # type: ignore[arg-type]
        except TypeError:
            logger.warning(
                "plugin %s overlay region %r sample() returned %s, not a sequence of "
                "widgets; no position-mode preview for it",
                plugin_id,
                key,
                type(made).__name__,
            )
            return []
        try:
            items = list(iterator)
        except Exception:
            # Iterating is a call into the plugin too, and a separate one: a
            # generator can yield a widget and THEN raise, and a custom
            # ``__iter__``/``__next__`` can raise anything at all. Narrowing
            # this to TypeError left every other exception escaping exactly as
            # before.
            logger.exception(
                "plugin %s overlay region %r sample() raised while being iterated",
                plugin_id,
                key,
            )
            return []
        # Screened on QWidget, NOT on ``deleteLater``: QObject has that method
        # too, so a bare QObject passed and was handed to the overlay, where
        # ``_discard_preview``'s ``layout.removeWidget(item)`` rejects it with
        # a TypeError — on the way OUT of position mode, so the overlay never
        # finishes relocking and is left interactive over the game.
        kept = [item for item in items if isinstance(item, QWidget)]
        if len(kept) != len(items):
            logger.warning(
                "plugin %s overlay region %r sample() returned %d item(s) that are not "
                "QWidgets (%s); they are dropped from the position-mode preview",
                plugin_id,
                key,
                len(items) - len(kept),
                ", ".join(sorted({type(i).__name__ for i in items if not isinstance(i, QWidget)})),
            )
        return kept

    return build


@dataclass
class _LivePluginUi:
    """The subscriber that makes plugin surfaces follow the plugin (#45).

    One instance per launch, subscribed to ``PluginHost.on_ui_build`` and
    ``on_ui_teardown`` by ``PluginUi.attach_live``. Everything it touches is
    a collection owned by ``create_app`` or by a window, mutated in place —
    which is what lets a toggle in Settings > Plugins reach the tray menu,
    the chat commands, the layout manager, the skin sweep and the settings
    sidebar without any of them being rebuilt.

    Both entry points are called from the GUI thread (the host runs the hook
    inline from ``set_enabled``) and both are fully guarded: an add-on may
    not break the settings window it is being toggled in.
    """

    ui: PluginUi
    plugin_host: PluginHost
    settings: Settings
    save: Callable[[], None]
    bridge: QtEventBridge
    window_handles: dict[str, Any]
    settings_window: Any
    layouts: Any
    legacy_app: Any
    chrome_surfaces: list[Any]
    apply_appearance: Callable[[], None]
    event_overlay: Any = None

    def materialize(self, loaded: LoadedPlugin) -> None:
        """Build and register everything a just-activated plugin declared."""
        plugin_id = loaded.plugin_id
        if plugin_id is None:
            return
        built = False
        for spec, widget in _build_plugin_windows(loaded, self.settings, self.save, self.bridge):
            registered = _register_window(self.ui, loaded, spec, widget, self.window_handles)
            if registered is None:
                widget.deleteLater()
                continue
            window_key, _provisional = registered
            with _isolated(f"plugin {plugin_id} window registration"):
                self.layouts.add_window(window_key, widget)
            self.chrome_surfaces.append(widget)
            built = True
        for spec, widget in _build_plugin_regions(
            loaded, self.settings, self.save, self.bridge, self.event_overlay
        ):
            if _register_region(self.ui, loaded, spec, widget, self.event_overlay) is None:
                _discard_region_widget(widget)
                continue
            # Dressed from the current skin by its own constructor; this is
            # what carries it through every LATER change.
            self.chrome_surfaces.append(widget)
        for spec in list(loaded.page_specs):
            self.ui.extra_pages.append(spec)
            self.ui.surfaces.setdefault(plugin_id, _PluginSurfaces()).page_specs.append(spec)
            with _isolated(f"plugin {plugin_id} settings page"):
                self.settings_window.add_page(spec)
        if built:
            self.claim_tray_labels(plugin_id)
            # create_app merged the startup command table by hand once; the
            # live path has to keep that merge true, and re-merging every
            # plugin command is idempotent (same keys, same widgets).
            self.window_handles.update(self.ui.command_handles)
            # The new windows are undressed until a skin lands on them, and
            # the Settings > Windows grid is built once per row set.
            with _isolated("settings window rows"):
                self.settings_window.set_plugin_window_rows(self.ui.window_rows)
            with _isolated("skin sweep"):
                self.apply_appearance()

    def claim_tray_labels(self, plugin_id: str) -> None:
        """Give one plugin's windows real tray labels, then add them.

        A plugin names its own window, so a title of "Settings" or "Console"
        collides with an app entry — and the tray dict is last-write-wins,
        so the built-in would simply disappear until the add-on was disabled.
        ``_register_window`` cannot catch that: at launch it runs before the
        tray exists, and the only labels it can see are other plugins'. This
        runs once the tray is real, for the startup batch and for every live
        enable, and disambiguates with the plugin id — which is also the
        answer to "whose window is this?", the question a duplicate title
        leaves the user asking.
        """
        surfaces = self.ui.surfaces.get(plugin_id)
        if surfaces is None:
            return
        claimed: list[str] = []
        for provisional in surfaces.tray_labels:
            widget = self.ui.tray.pop(provisional, None)
            if widget is None:
                continue
            label = self._free_tray_label(provisional, plugin_id)
            self.ui.tray[label] = widget
            with _isolated(f"plugin {plugin_id} tray entry"):
                self.legacy_app.add_backend_window(label, widget, plugin=True)
            claimed.append(label)
        surfaces.tray_labels = claimed

    def _free_tray_label(self, title: str, plugin_id: str) -> str:
        label = title
        attempt = 1
        while self._tray_label_taken(label):
            attempt += 1
            suffix = f" ({plugin_id})" if attempt == 2 else f" ({plugin_id} {attempt})"
            label = f"{title}{suffix}"
        return label

    def _tray_label_taken(self, label: str) -> bool:
        if label in self.ui.tray:
            return True
        try:
            return bool(self.legacy_app.has_backend_window(label))
        except Exception:
            # An embedder with a tray that cannot answer: fall back to what
            # this layer knows rather than refusing to register anything.
            logger.exception("tray could not be asked about %r", label)
            return False

    def retire(self, plugin_id: str) -> None:
        """Unregister and destroy everything that plugin had on screen.

        Runs after the host unwound the plugin's registrations, which is the
        order that matters: hiding a window whose plugin is still subscribed
        would leave its QTimers firing into a widget nobody manages, and
        destroying it before the unwind could pull the ground out from under
        a handler mid-call.
        """
        surfaces = self.ui.surfaces.pop(plugin_id, None)
        if surfaces is None:
            return
        for spec in surfaces.page_specs:
            with _isolated(f"plugin {plugin_id} settings page"):
                self.settings_window.remove_page(spec)
            if spec in self.ui.extra_pages:
                self.ui.extra_pages.remove(spec)
        for command_key in surfaces.command_keys:
            self.window_handles.pop(command_key, None)
            self.ui.command_handles.pop(command_key, None)
        for label in surfaces.tray_labels:
            self.ui.tray.pop(label, None)
            with _isolated(f"plugin {plugin_id} tray entry"):
                self.legacy_app.remove_backend_window(label)
        for region_key in surfaces.region_keys:
            self.ui.regions_by_key.pop(region_key, None)
            with _isolated(f"plugin {plugin_id} overlay region"):
                # Hands the widget back hidden and unparented; the persisted
                # placement stays behind on purpose, so re-enabling the plugin
                # brings its region back where the user put it.
                self.event_overlay.remove_region(region_key)
        for widget in surfaces.region_widgets:
            if widget in self.chrome_surfaces:
                self.chrome_surfaces.remove(widget)
            with _isolated(f"plugin {plugin_id} overlay region teardown"):
                widget.hide()
                widget.deleteLater()
        for window_key in surfaces.window_keys:
            self.ui.windows_by_key.pop(window_key, None)
            with _isolated(f"plugin {plugin_id} layout entry"):
                self.layouts.remove_window(window_key)
        dropped = set(surfaces.window_keys)
        self.ui.window_rows[:] = [row for row in self.ui.window_rows if row[1] not in dropped]
        for widget in surfaces.widgets:
            if widget in self.chrome_surfaces:
                self.chrome_surfaces.remove(widget)
            with _isolated(f"plugin {plugin_id} window teardown"):
                # Hide first: deleteLater() only schedules the destruction, so
                # a visible window would otherwise linger on screen until the
                # event loop next spins.
                widget.hide()
                widget.deleteLater()
        if surfaces.window_keys:
            with _isolated("settings window rows"):
                self.settings_window.set_plugin_window_rows(self.ui.window_rows)


def _discard_region_widget(widget: Any) -> None:
    """Dispose a region widget the overlay refused, without raising.

    The type screen in ``_build_plugin_regions`` means this only ever sees a
    QWidget, so it cannot normally fail. It is guarded anyway because it is
    called OUTSIDE ``_isolated`` on the startup sweep, where one exception
    costs every other plugin its UI — the invariant and the disposal are far
    enough apart that the second should not depend on the first holding.
    """
    try:
        widget.deleteLater()
    except Exception:
        logger.exception("discarding a refused overlay region widget failed")


@contextmanager
def _isolated(what: str) -> Iterator[None]:
    """Log and swallow — no add-on may break the window it is toggled in."""
    try:
        yield
    except Exception:
        logger.exception("%s failed", what)


def schedule_update_check(
    plugin_host: PluginHost,
    app_version: str,
    *,
    delay_ms: int = PLUGIN_UPDATE_CHECK_DELAY_MS,
) -> None:
    """Poll for plugin updates a little after launch, quietly.

    Lives here rather than in ``app.py`` because this module holds the line
    that ``create_app`` has exactly two plugin import sites; adding a third
    would erode the invariant ``test_master_toggle.py`` exists to protect.

    No Qt signal is needed on the way back: the only consumer is
    ``cache_update_check``, a single assignment of an immutable result that
    nothing reacts to at the moment it lands. Settings > Plugins reads it on
    the GUI thread whenever it is next built. (A settings window already open
    when the result arrives will not refresh until the user clicks Check for
    updates — accepted for now.)

    ``delay_ms`` is a parameter so the whole path is testable without waiting.
    """
    from PySide6.QtCore import QTimer

    from nparseplus.core.plugins.updatecheck import check_for_updates

    def start() -> None:
        def work() -> None:
            try:
                plugin_host.cache_update_check(
                    check_for_updates(
                        plugin_host.installed_for_update_check(),
                        plugin_host.enabled_registries(),
                        sdk_version=plugin_host.sdk_version,
                        app_version=app_version,
                    )
                )
            except Exception:
                # A registry being down must never surface as a crash — the
                # user did not ask for this check, it just happens.
                logger.exception("plugin update check failed")

        threading.Thread(target=work, name="plugin-update-check", daemon=True).start()

    QTimer.singleShot(delay_ms, start)


def _add_window_row(
    ui: PluginUi, loaded: LoadedPlugin, spec: Any, widget: Any, window_key: str
) -> None:
    """Record the window's Settings > Windows row, if it can have one.

    Only widgets built on the overlay recipe qualify: ``PluginWindowSpec``
    promises no more than ``.toggle()``/``.isVisible()``, and every QWidget
    has ``setWindowOpacity``, so a bare widget would get a slider that
    previews live and is then silently dropped on Apply (and would fabricate
    a ``settings.windows`` entry nobody reads).
    """
    if not hasattr(widget, "apply_window_state"):
        return
    assert loaded.meta is not None
    # meta.name has no min-length validator, so "" is a legal plugin name;
    # LoadedPlugin.display_name only covers meta being absent entirely.
    plugin_name = (loaded.meta.name or "").strip() or loaded.meta.id
    window_title = (spec.title or "").strip() or spec.key
    label = f"{plugin_name} — {window_title}"
    if any(existing == label for existing, _key, _widget in ui.window_rows):
        # One plugin, two windows with the same title: the plugin name prefix
        # cannot separate them, so fall back to the key (unique per plugin).
        label = f"{label} ({spec.key})"
    ui.window_rows.append((label, window_key, widget))


def _plugin_command_key(loaded: LoadedPlugin, spec_key: str, command_key: str | None) -> str:
    """In-game toggle_<key> name: declared or <plugin_id>_<key>, \\w-sanitized."""
    assert loaded.meta is not None
    raw = command_key or f"{loaded.meta.id}_{spec_key}"
    return re.sub(r"\W", "_", raw)


def _build_plugin_windows(
    loaded: LoadedPlugin,
    settings: Settings,
    save: Callable[[], None],
    bridge: QtEventBridge,
) -> list[tuple[Any, Any]]:
    """Build ONE plugin's declared windows; every factory is guarded.

    Per-plugin rather than a sweep because #45 needs exactly this for one
    add-on enabled mid-session; the startup sweep is now a loop over it, so
    a window built at launch and one built live go through the same code.
    """
    from nparseplus_sdk.plugin import PluginWindowContext

    assert loaded.meta is not None
    built: list[tuple[Any, Any]] = []
    for spec in list(loaded.window_specs):
        window_key = f"plugin.{loaded.meta.id}.{spec.key}"
        wctx = PluginWindowContext(
            settings=settings,
            window_key=window_key,
            title=spec.title,
            default_geometry=spec.default_geometry,
            on_save=save,
            bridge=bridge,
        )
        try:
            widget = spec.factory(wctx)
        except Exception:
            logger.exception(
                "plugin %s window %r factory failed; window skipped",
                loaded.meta.id,
                spec.key,
            )
            continue
        if widget is None:
            logger.warning("plugin %s window %r factory returned None", loaded.meta.id, spec.key)
            continue
        built.append((spec, widget))
    return built


def _build_plugin_regions(
    loaded: LoadedPlugin,
    settings: Settings,
    save: Callable[[], None],
    bridge: QtEventBridge,
    event_overlay: Any,
) -> list[tuple[Any, Any]]:
    """Build ONE plugin's declared event-overlay regions; factories guarded.

    Per-plugin like ``_build_plugin_windows``, so a region built at launch and
    one built for an add-on enabled mid-session go through the same code.

    With no overlay to put them on, the regions are dropped with a line in the
    log — an embedder without an event overlay is a legitimate configuration,
    a plugin silently missing half its UI is not.
    """
    from PySide6.QtWidgets import QWidget

    from nparseplus.ui.pluginregion import enforce_non_interactive
    from nparseplus_sdk.plugin import OverlayRegionContext, OverlayRegionSpec

    assert loaded.meta is not None
    specs = list(loaded.overlay_region_specs)
    if not specs:
        return []
    if event_overlay is None:
        logger.warning(
            "plugin %s declared %d overlay region(s) but this app has no event overlay; "
            "they are not shown",
            loaded.meta.id,
            len(specs),
        )
        return []
    built: list[tuple[Any, Any]] = []
    for index, spec in enumerate(specs):
        if not isinstance(spec, OverlayRegionSpec):
            # BEFORE the first attribute access, and that ordering is the
            # whole point: ``add_overlay_region`` appends whatever it is
            # given, so ``ctx.add_overlay_region(None)`` used to reach
            # ``spec.key`` here — outside every guard — and abort the startup
            # sweep for EVERY plugin, taking the plugin manager page with it.
            # The report therefore names the position and the type; it cannot
            # name the key, because dereferencing is exactly what is unsafe.
            logger.warning(
                "plugin %s declared overlay region #%d as %s, not an OverlayRegionSpec; "
                "region skipped",
                loaded.meta.id,
                index,
                type(spec).__name__,
            )
            continue
        if not isinstance(spec.key, str):
            # ``spec.key`` is safe to READ (attribute access calls nothing),
            # but interpolating it below calls ``__str__``, which is the
            # plugin's code and can raise — outside every guard, taking the
            # whole sweep and the plugin manager page with it. Same shape as
            # the non-spec screen above, one field in: the report names the
            # position and the type and never the value.
            logger.warning(
                "plugin %s declared overlay region #%d with a %s key, not a str; region skipped",
                loaded.meta.id,
                index,
                type(spec.key).__name__,
            )
            continue
        region_key = f"plugin.{loaded.meta.id}.{spec.key}"
        # The context is built INSIDE the guard with the factory, not before
        # it: assembling it reaches into ``event_overlay``, so a stand-in that
        # cannot supply the content hook would otherwise abort the whole sweep
        # rather than costing one region — the same isolation promise the
        # factory itself gets.
        try:
            rctx = OverlayRegionContext(
                settings=settings,
                region_key=region_key,
                title=spec.title,
                on_save=save,
                # Bound to the key rather than to the record: the overlay
                # ignores a key it no longer holds, so a region notifying
                # after it was retired is a no-op rather than a reference into
                # a dead record.
                on_content_changed=_region_content_hook(event_overlay, region_key),
                bridge=bridge,
            )
            widget = spec.factory(rctx)
        except Exception:
            logger.exception(
                "plugin %s overlay region %r could not be built; region skipped",
                loaded.meta.id,
                spec.key,
            )
            continue
        if widget is None:
            logger.warning(
                "plugin %s overlay region %r factory returned None", loaded.meta.id, spec.key
            )
            continue
        if not isinstance(widget, QWidget):
            # Screened HERE, where the factory result is first seen, because
            # everything downstream assumes a real widget: ``add_region``
            # reaches for ``layout()``/``setParent()`` inside an isolation
            # guard, but the refusal path then calls ``deleteLater()``
            # OUTSIDE one — and on the startup sweep that second exception
            # aborts ``build_plugin_ui`` for EVERY plugin and takes the plugin
            # manager page with it. One bad factory must stay one bad factory.
            #
            # Enforcing the type is not a narrowing: ``OverlayRegionSpec``
            # already documents that the factory returns a QWidget, and a
            # region host is placed, resized, moved and stylesheeted by the
            # overlay, so nothing else can stand in.
            logger.warning(
                "plugin %s overlay region %r factory returned %s, not a QWidget; region skipped",
                loaded.meta.id,
                spec.key,
                type(widget).__name__,
            )
            continue
        # EVERY accepted widget is sealed here, not just a PluginOverlayRegion.
        # The display-only guarantee is a promise about every region, and the
        # factory may return a plain QWidget — which is supported, and which
        # nothing else makes input-transparent. Unsealed, it receives the click
        # in position mode (where the overlay drops WindowTransparentForInput)
        # and its own rectangle becomes impossible to drag, because the press
        # never falls through to the overlay's hit-test.
        with _isolated(f"plugin {loaded.meta.id} overlay region {spec.key!r} seal"):
            enforce_non_interactive(widget)
        built.append((spec, widget))
    return built


def _region_content_hook(event_overlay: Any, region_key: str) -> Callable[[], None]:
    """``OverlayRegionContext.on_content_changed`` for one region, guarded.

    A plugin calls this from its own timers and signal handlers, where an
    exception has nowhere to go but the Qt event loop of a translucent,
    always-on-top window over a running game.

    **The overlay is held WEAKLY**, for the reason ``eventoverlay.weak_hook``
    exists (#154). The overlay owns the region's host widget, the widget holds
    its ``OverlayRegionContext``, and the context holds this closure — so
    closing over the overlay strongly puts the WINDOW in a Python reference
    cycle. That takes its destruction away from refcounting and hands it to
    the cyclic collector, which runs whenever it likes, and a QWidget freed
    there rather than by Qt is a use-after-free the next repaint walks into.
    It segfaulted the suite from inside ``paintEvent`` before #154; a plugin
    region is the one thing that could reintroduce it, and a test pins the
    lifetime.

    ``WeakMethod`` rather than a ``ref`` to the window because the bound
    method is what is being called; the underlying function is a class
    attribute and never dies on its own, so this tracks the instance. A dead
    overlay answers by doing nothing — unobservable in practice, since a
    region only reaches this while the overlay is showing it.
    """
    resolve: Callable[[], Any]
    try:
        resolve = weakref.WeakMethod(event_overlay.region_content_changed)
    except TypeError:  # pragma: no cover - a stand-in whose hook is not a method
        # Held strongly, deliberately: the cycle above runs overlay -> host
        # widget -> context -> here, and something that is not a real QWidget
        # overlay is not in it.
        strong = event_overlay.region_content_changed
        resolve = lambda: strong  # noqa: E731

    def notify() -> None:
        target = resolve()
        if target is None:
            return
        try:
            target(region_key)
        except Exception:
            logger.exception("overlay region %r content change failed", region_key)

    return notify


def _plugins_with_regions(plugin_host: PluginHost) -> list[LoadedPlugin]:
    """The active plugins declaring at least one region, in load order."""
    seen: list[LoadedPlugin] = []
    for loaded, _spec in plugin_host.overlay_region_specs():
        if loaded not in seen:
            seen.append(loaded)
    return seen


def _materialize_plugin_windows(
    plugin_host: PluginHost,
    settings: Settings,
    save: Callable[[], None],
    bridge: QtEventBridge,
) -> list[tuple[LoadedPlugin, Any, Any]]:
    """Build every active plugin's declared windows (the startup sweep)."""
    built: list[tuple[LoadedPlugin, Any, Any]] = []
    seen: list[LoadedPlugin] = []
    for loaded, _spec in plugin_host.window_specs():
        if loaded not in seen:
            seen.append(loaded)
    for loaded in seen:
        built += [
            (loaded, spec, widget)
            for spec, widget in _build_plugin_windows(loaded, settings, save, bridge)
        ]
    return built
