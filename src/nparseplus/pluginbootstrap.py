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
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable
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
class PluginUi:
    """What the plugin subsystem contributes to the assembled UI.

    Empty in every field when plugins are off, so ``create_app`` can splat
    these unconditionally instead of branching at each use.
    """

    windows_by_key: dict[str, Any] = field(default_factory=dict)  # "plugin.<id>.<key>"
    command_handles: dict[str, Any] = field(default_factory=dict)  # chat toggle_<name>
    tray: dict[str, Any] = field(default_factory=dict)  # tray label -> widget
    extra_pages: list[Any] = field(default_factory=list)
    # Settings > Windows grid rows: (label, window key, widget). Only windows
    # built on the overlay recipe appear here — see build_plugin_ui.
    window_rows: list[tuple[str, str, Any]] = field(default_factory=list)


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
            continue
        ui.windows_by_key[window_key] = widget
        command_key = _plugin_command_key(loaded, spec.key, spec.command_key)
        if command_key in window_handles or command_key in ui.command_handles:
            logger.warning(
                "plugin %s window command %r collides; chat toggle skipped",
                loaded.meta.id,
                command_key,
            )
        else:
            ui.command_handles[command_key] = widget
        label = spec.title
        if label in ui.tray:
            label = f"{spec.title} ({loaded.meta.id})"
        ui.tray[label] = widget
        _add_window_row(ui, loaded, spec, widget, window_key)

    ui.extra_pages.extend(spec for _loaded, spec in plugin_host.page_specs())
    if settings.plugins.update_check:
        schedule_update_check(plugin_host, app_version)
    return ui


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


def _materialize_plugin_windows(
    plugin_host: PluginHost,
    settings: Settings,
    save: Callable[[], None],
    bridge: QtEventBridge,
) -> list[tuple[LoadedPlugin, Any, Any]]:
    """Build each active plugin's declared windows; every factory is guarded."""
    from nparseplus_sdk.plugin import PluginWindowContext

    built: list[tuple[LoadedPlugin, Any, Any]] = []
    for loaded, spec in plugin_host.window_specs():
        assert loaded.meta is not None
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
        built.append((loaded, spec, widget))
    return built
