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
    command_handles: dict[str, Any] = field(default_factory=dict)  # chat toggle_<name>
    tray: dict[str, Any] = field(default_factory=dict)  # tray label -> widget
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
        settings window grew add/remove methods, and ``chrome_surfaces`` is
        the list a skin change sweeps.
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
) -> PluginUi:
    """Run consent, activate, and materialize each plugin's Qt contributions."""
    from nparseplus.ui.pluginconsent import run_consent_prompts
    from nparseplus.ui.pluginmanager import plugin_manager_page_spec

    ui = PluginUi()
    ui.extra_pages.append(plugin_manager_page_spec(plugin_host, app_version))
    run_consent_prompts(plugin_host)
    plugin_host.activate_enabled()

    for loaded, spec, widget in _materialize_plugin_windows(plugin_host, settings, save, bridge):
        _register_window(ui, loaded, spec, widget, window_handles)

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
            window_key, tray_label = registered
            with _isolated(f"plugin {plugin_id} window registration"):
                self.layouts.add_window(window_key, widget)
                self.legacy_app.add_backend_window(tray_label, widget)
            self.chrome_surfaces.append(widget)
            built = True
        for spec in list(loaded.page_specs):
            self.ui.extra_pages.append(spec)
            self.ui.surfaces.setdefault(plugin_id, _PluginSurfaces()).page_specs.append(spec)
            with _isolated(f"plugin {plugin_id} settings page"):
                self.settings_window.add_page(spec)
        if built:
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
