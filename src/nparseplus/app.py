"""M1 application composition: new Qt-free backend + hybrid UI.

``run_app`` wires:
- the NEW backend (``composition.build_backend``: log driver thread, parser
  pipeline, timers, triggers) driven by the NEW pydantic ``Settings``, and
- the legacy ``NomnsParse`` QApplication (maps + discord windows, tray menu),
  which in backend mode is fed log lines through ``QtEventBridge`` instead of
  its old QFileSystemWatcher log reader, and
- the new ``SpellTimerWindow`` (replaces the legacy spells parser window).
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from nparseplus.composition import Backend, build_backend
from nparseplus.config.settings import (
    DebouncedSaver,
    MapMarkerStore,
    Settings,
    WindowState,
    load_settings,
    save_settings,
)
from nparseplus.core.events import WindowCommandEvent
from nparseplus.core.player import tracking_distance
from nparseplus.ui import theme


class _OverlayPositioner:
    """Tray-menu adapter: 'toggling' this enters/exits the event overlay's
    position mode (checkable item shows whether positioning is active)."""

    def __init__(self, overlay) -> None:
        self._overlay = overlay

    def toggle(self) -> None:
        self._overlay.set_edit_mode(not self._overlay.is_edit_mode())

    def isVisible(self) -> bool:
        return self._overlay.is_edit_mode()


if TYPE_CHECKING:
    from collections.abc import Callable

    from nparseplus.helpers.application import NomnsParse
    from nparseplus.ui.consolewindow import ConsoleWindow
    from nparseplus.ui.dpswindow import DpsMeterWindow
    from nparseplus.ui.eventoverlay import EventOverlayWindow
    from nparseplus.ui.mobinfo import MobInfoWindow
    from nparseplus.ui.qtbridge import QtEventBridge
    from nparseplus.ui.spellwindow import SpellTimerWindow
    from nparseplus.ui.windowlayouts import WindowLayoutManager

# Optional override of the settings.json location (debug/e2e hook).
SETTINGS_ENV_VAR = "NPARSEPLUS_SETTINGS"


def _runtime_env_defaults(
    platform: str,
    environ: dict[str, str],
    *,
    frozen: bool,
    userns_restricted: bool,
) -> dict[str, str]:
    """Linux env defaults to ADD (never overriding explicit values).

    - QT_QPA_PLATFORM=xcb: the overlay recipe needs keep-above/self-positioning,
      which native Wayland windows don't get; EQ under WINE is X11 too. Mirrors
      packaging/flatpak/nparseplus.sh so tarball/source runs behave the same.
    - QTWEBENGINE_DISABLE_SANDBOX=1: Chromium's sandbox needs unprivileged user
      namespaces; frozen bundles and userns-restricted kernels (Ubuntu 24.04
      AppArmor default) crash the Discord overlay's render processes without
      this. Scoped narrowly — a user can force it off with an explicit "0".
    """
    out: dict[str, str] = {}
    if not platform.startswith("linux"):
        return out
    if "QT_QPA_PLATFORM" not in environ:
        out["QT_QPA_PLATFORM"] = "xcb"
    if "QTWEBENGINE_DISABLE_SANDBOX" not in environ and (frozen or userns_restricted):
        out["QTWEBENGINE_DISABLE_SANDBOX"] = "1"
    return out


def _apply_runtime_env_defaults() -> None:
    """Apply ``_runtime_env_defaults`` — call before any PySide6 import."""
    restricted = False
    try:
        knob = Path("/proc/sys/kernel/apparmor_restrict_unprivileged_userns")
        restricted = knob.read_text().strip() == "1"
    except OSError:
        pass
    defaults = _runtime_env_defaults(
        sys.platform,
        os.environ,  # type: ignore[arg-type]
        frozen=bool(getattr(sys, "frozen", False)),
        userns_restricted=restricted,
    )
    for key, value in defaults.items():
        os.environ[key] = value
        print(f"nparseplus: defaulting {key}={value}", file=sys.stderr)


def _ensure_data_cwd() -> None:
    """Legacy modules open ``data/...`` relative to CWD.

    Frozen (PyInstaller): both data roots are bundled under ``sys._MEIPASS``
    (see packaging/nparseplus.spec), so chdir there — a Finder launch starts
    with CWD ``/``. From a source checkout, locate the project root that
    holds ``data/`` instead.
    """
    if getattr(sys, "frozen", False):
        os.chdir(sys._MEIPASS)  # type: ignore[attr-defined]
        return
    if Path("data").is_dir():
        return
    for parent in Path(__file__).resolve().parents:
        if (parent / "data").is_dir():
            os.chdir(parent)
            return


def _apply_window_command(event: object, window_handles: dict[str, object]) -> None:
    """show_/hide_/toggle_<window> typed in game (core WindowChatCommands).

    ``toggle()`` owns each window's persistence (legacy and new alike), so
    show/hide only flip when the state actually differs.
    """
    if not isinstance(event, WindowCommandEvent):
        return
    window = window_handles.get(event.window)
    if window is None:
        return
    if event.action == "toggle" or (event.action == "show") != window.isVisible():  # type: ignore[attr-defined]
        window.toggle()  # type: ignore[attr-defined]


@dataclass
class AppContext:
    """Everything ``run_app`` builds, exposed for tests/e2e drivers."""

    app: NomnsParse
    backend: Backend
    bridge: QtEventBridge
    spell_window: SpellTimerWindow
    dps_window: DpsMeterWindow
    mob_info_window: MobInfoWindow
    console_window: ConsoleWindow
    event_overlay: EventOverlayWindow
    window_layouts: WindowLayoutManager
    settings: Settings
    save: Callable[[], None]


def create_app(argv: list[str], settings_file: Path | None = None) -> AppContext:
    _apply_runtime_env_defaults()
    _ensure_data_cwd()

    if settings_file is None:
        env_path = os.environ.get(SETTINGS_ENV_VAR)
        if env_path:
            settings_file = Path(env_path)
    settings = load_settings(settings_file)

    def _save_settings_now() -> None:
        save_settings(settings, settings_file)

    # Driver-thread handlers persist per-character profile changes through
    # this (thread-safe, coalesced); the GUI's save() below writes directly.
    saver = DebouncedSaver(_save_settings_now)
    backend = build_backend(settings, request_save=saver.request_save)

    # Legacy imports come last: helpers.application loads nparse.config.json
    # from the CWD at import time and pulls in Qt.
    from PySide6.QtCore import QCoreApplication, Qt
    from PySide6.QtGui import QFontDatabase, QIcon

    from nparseplus.helpers import config as legacy_config
    from nparseplus.helpers import resource_path
    from nparseplus.helpers.application import NomnsParse
    from nparseplus.ui.consolewindow import ConsoleWindow
    from nparseplus.ui.dpswindow import DpsMeterWindow
    from nparseplus.ui.eventoverlay import EventOverlayWindow
    from nparseplus.ui.mobinfo import MobInfoWindow
    from nparseplus.ui.qtbridge import QtEventBridge
    from nparseplus.ui.settingswindow import UnifiedSettingsWindow
    from nparseplus.ui.spellwindow import SpellTimerWindow
    from nparseplus.ui.triggereditor import TriggerEditorWindow
    from nparseplus.ui.windowlayouts import WindowLayoutManager

    # QtWebEngine (Discord overlay) requires shared GL contexts before the
    # QApplication exists. Today the helpers.application import chain happens
    # to pull QtWebEngineWidgets first, which also satisfies it — set the
    # attribute explicitly so a refactor of that import order can't crash.
    QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)

    app = NomnsParse(list(argv), backend=backend)
    theme.set_theme(settings.general.theme)
    with open(resource_path(os.path.join("data", "ui", theme.stylesheet_filename()))) as css:
        app.setStyleSheet(css.read())
    app.setWindowIcon(QIcon(resource_path(os.path.join("data", "ui", "icon.png"))))
    app.setQuitOnLastWindowClosed(False)
    QFontDatabase.addApplicationFont(
        resource_path(os.path.join("data", "fonts", "NotoSans-Regular.ttf"))
    )
    QFontDatabase.addApplicationFont(
        resource_path(os.path.join("data", "fonts", "NotoSans-Bold.ttf"))
    )

    def save() -> None:
        _save_settings_now()

    bridge = QtEventBridge(backend.bus)
    spell_window = SpellTimerWindow(backend, on_save=save)
    dps_window = DpsMeterWindow(backend, on_save=save)
    mob_info_window = MobInfoWindow(settings, backend.mob_info, on_save=save)
    console_window = ConsoleWindow(settings, on_save=save)
    overlay_state = settings.windows.setdefault("overlay", WindowState())
    event_overlay = EventOverlayWindow(
        clear_after_s=settings.general.overlay_text_seconds,
        ch_lane_retention_s=settings.general.ch_lane_retention_seconds,
        state=overlay_state,
        on_save=save,
    )
    trigger_editor = TriggerEditorWindow(settings, backend.trigger_engine, on_save=save)

    def _repaint_maps() -> None:
        if app.maps_window is not None:
            app.maps_window._map.update_()

    window_handles = {
        "maps": app.maps_window,
        "discord": app._parsers_dict.get("discord"),
        "spells": spell_window,
        "dps": dps_window,
        "mobinfo": mob_info_window,
        "console": console_window,
        "triggereditor": trigger_editor,
    }
    settings_window = UnifiedSettingsWindow(
        settings,
        on_save=save,
        on_log_dir_changed=backend.driver.set_log_dir,
        on_audio_changed=backend.rebuild_speaker,
        legacy_config=legacy_config.data,
        on_legacy_save=legacy_config.save,
        notify_legacy=app._signals["settings"].config_updated.emit,
        repaint_maps=_repaint_maps,
        window_handles=window_handles,
        backend_player=backend.player,
        zones=backend.zones,
    )
    layout_windows = {
        **window_handles,
        "settings": settings_window,
        "overlay": event_overlay,
    }
    window_layouts = WindowLayoutManager(
        settings,
        {key: window for key, window in layout_windows.items() if window is not None},
        on_save=save,
        legacy_config=legacy_config.data,
        on_legacy_save=legacy_config.save,
        notify=lambda message: app._system_tray.showMessage("Window layouts", message, msecs=3000),
    )

    bridge.event_received.connect(lambda event: _apply_window_command(event, window_handles))
    bridge.event_received.connect(event_overlay.handle_event)
    bridge.event_received.connect(console_window.handle_event)
    bridge.event_received.connect(settings_window.handle_backend_event)
    if app.maps_window is not None:
        # Remote (shared) player dots; the coordinator has already filtered
        # self-echo and server mismatches on the driver thread.
        bridge.event_received.connect(app.maps_window.handle_remote_event)
        app.maps_window.tracking_radius_provider = lambda: tracking_distance(
            backend.player.player_class, backend.player.tracking_skill
        )
        # Persistent map markers: the maps window loaded its first map before
        # the store existed, so restore that zone's markers now.
        app.maps_window._map.marker_store = MapMarkerStore(
            settings, request_save=saver.request_save
        )
        app.maps_window._map.restore_markers()
    app.attach_backend_ui(
        bridge,
        spell_window,
        save,
        windows={
            "Settings": settings_window,
            "DPS Meter": dps_window,
            "Mob Info": mob_info_window,
            "Console": console_window,
            "Trigger Editor": trigger_editor,
            "Position Event Overlay": _OverlayPositioner(event_overlay),
        },
        window_layouts=window_layouts,
    )
    app.aboutToQuit.connect(backend.stop)
    app.aboutToQuit.connect(saver.flush)

    # Persist the settled settings immediately: on a fresh install nothing
    # else may write settings.json this session, and the app itself creates
    # a default legacy nparse.config.json — without this, the NEXT launch
    # would "migrate" that self-created file (sharing.enabled=False -> mode
    # off) and silently turn sharing off.
    save()

    return AppContext(
        app=app,
        backend=backend,
        bridge=bridge,
        spell_window=spell_window,
        dps_window=dps_window,
        mob_info_window=mob_info_window,
        console_window=console_window,
        event_overlay=event_overlay,
        window_layouts=window_layouts,
        settings=settings,
        save=save,
    )


def run_app(argv: list[str] | None = None, settings_file: Path | None = None) -> int:
    # Crash guard first: startup failures land in the log too. In the frozen
    # app stderr is invisible, so without this a crash leaves no evidence.
    from nparseplus import crashguard
    from nparseplus.config.paths import ensure_log_dir

    crash_log = ensure_log_dir() / "crash.log"
    crashguard.install(crash_log)

    # Frozen app stderr is invisible (Finder launch), so warnings like the
    # pigparse reconnect reasons vanished. Mirror the nparseplus logger tree
    # to a rotating file next to the crash log.
    import logging
    from logging.handlers import RotatingFileHandler

    handler = RotatingFileHandler(
        crash_log.with_name("nparseplus.log"), maxBytes=1_000_000, backupCount=1
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    app_logger = logging.getLogger("nparseplus")
    app_logger.setLevel(logging.INFO)
    app_logger.addHandler(handler)

    ctx = create_app(list(sys.argv) if argv is None else list(argv), settings_file)
    ctx.backend.start()

    # Slot exceptions route through sys.excepthook, but depending on the
    # PySide6 version an exception can also propagate out of exec() — log
    # those and re-enter the loop instead of dying, capped against loops.
    remaining_restarts = 5
    while True:
        try:
            return ctx.app.exec()
        except Exception as exc:
            crashguard.log_exception(exc, crash_log, context="event loop")
            remaining_restarts -= 1
            if remaining_restarts < 0:
                raise
