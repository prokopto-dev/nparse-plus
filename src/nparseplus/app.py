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
from nparseplus.config.settings import Settings, WindowState, load_settings, save_settings


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

# Optional override of the settings.json location (debug/e2e hook).
SETTINGS_ENV_VAR = "NPARSEPLUS_SETTINGS"


def _ensure_data_cwd() -> None:
    """Legacy modules open ``data/...`` relative to CWD.

    Frozen (PyInstaller): both data roots are bundled under ``sys._MEIPASS``
    (see packaging/nparseplus.spec), so chdir there — a Finder launch starts
    with CWD ``/``. From a source checkout, locate the project root that
    holds ``data/`` instead.
    """
    if getattr(sys, "frozen", False):
        os.chdir(sys._MEIPASS)  # type: ignore[attr-defined]  # noqa: SLF001
        return
    if Path("data").is_dir():
        return
    for parent in Path(__file__).resolve().parents:
        if (parent / "data").is_dir():
            os.chdir(parent)
            return


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
    settings: Settings
    save: Callable[[], None]


def create_app(argv: list[str], settings_file: Path | None = None) -> AppContext:
    _ensure_data_cwd()

    if settings_file is None:
        env_path = os.environ.get(SETTINGS_ENV_VAR)
        if env_path:
            settings_file = Path(env_path)
    settings = load_settings(settings_file)
    backend = build_backend(settings)

    # Legacy imports come last: helpers.application loads nparse.config.json
    # from the CWD at import time and pulls in Qt.
    from PySide6.QtGui import QFontDatabase, QIcon

    from nparseplus.helpers import resource_path
    from nparseplus.helpers.application import NomnsParse
    from nparseplus.ui.consolewindow import ConsoleWindow
    from nparseplus.ui.dpswindow import DpsMeterWindow
    from nparseplus.ui.eventoverlay import EventOverlayWindow
    from nparseplus.ui.mobinfo import MobInfoWindow
    from nparseplus.ui.preferences import PreferencesWindow
    from nparseplus.ui.qtbridge import QtEventBridge
    from nparseplus.ui.spellwindow import SpellTimerWindow
    from nparseplus.ui.triggereditor import TriggerEditorWindow

    app = NomnsParse(list(argv), backend=backend)
    with open(resource_path(os.path.join("data", "ui", "_.css"))) as css:
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
        save_settings(settings, settings_file)

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
    preferences = PreferencesWindow(
        settings, on_save=save, on_log_dir_changed=backend.driver.set_log_dir
    )
    bridge.event_received.connect(event_overlay.handle_event)
    bridge.event_received.connect(console_window.handle_event)
    if app.maps_window is not None:
        # Remote (shared) player dots; the coordinator has already filtered
        # self-echo and server mismatches on the driver thread.
        bridge.event_received.connect(app.maps_window.handle_remote_event)
    app.attach_backend_ui(
        bridge,
        spell_window,
        save,
        windows={
            "DPS Meter": dps_window,
            "Mob Info": mob_info_window,
            "Console": console_window,
            "Trigger Editor": trigger_editor,
            "Preferences": preferences,
            "Position Event Overlay": _OverlayPositioner(event_overlay),
        },
    )
    app.aboutToQuit.connect(backend.stop)

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
        settings=settings,
        save=save,
    )


def run_app(argv: list[str] | None = None, settings_file: Path | None = None) -> int:
    ctx = create_app(list(sys.argv) if argv is None else list(argv), settings_file)
    ctx.backend.start()
    return ctx.app.exec()
