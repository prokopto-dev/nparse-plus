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
from nparseplus.config.settings import Settings, load_settings, save_settings

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

    Until data moves into the package, locate the project root that holds
    ``data/`` and chdir there when running from a source checkout.
    """
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
    from nparseplus.ui.qtbridge import QtEventBridge
    from nparseplus.ui.spellwindow import SpellTimerWindow

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
    event_overlay = EventOverlayWindow(clear_after_s=settings.general.overlay_text_seconds)
    bridge.event_received.connect(event_overlay.handle_event)
    bridge.event_received.connect(console_window.handle_event)
    app.attach_backend_ui(
        bridge,
        spell_window,
        save,
        windows={
            "DPS Meter": dps_window,
            "Mob Info": mob_info_window,
            "Console": console_window,
        },
    )
    app.aboutToQuit.connect(backend.stop)

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
