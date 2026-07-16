import os
import webbrowser
from pathlib import Path

from packaging.version import Version
from PySide6.QtCore import Qt
from PySide6.QtGui import QCursor, QIcon
from PySide6.QtWidgets import QApplication, QFileDialog, QMenu, QSystemTrayIcon

from nparseplus.core.events import LineEvent
from nparseplus.helpers import config, get_version, logreader, resource_path
from nparseplus.helpers.location_service import LocationSharingService, LocationSharingSignals
from nparseplus.helpers.logreader import LogReaderSignals
from nparseplus.helpers.settings import SettingsSignals, SettingsWindow
from nparseplus.parsers.discord import Discord
from nparseplus.parsers.maps import Maps
from nparseplus.parsers.maps.window import MapsSignals
from nparseplus.parsers.spells import Spells

config.load("nparse.config.json")
# validate settings file
config.verify_settings()

import nparseplus

CURRENT_VERSION = Version(nparseplus.__version__)
if config.data["general"]["update_check"]:
    ONLINE_VERSION = get_version()
else:
    ONLINE_VERSION = CURRENT_VERSION


class NomnsParse(QApplication):
    """Application Control.

    TRANSITIONAL (M1): when constructed with ``backend=...`` (the new Qt-free
    core from ``nparseplus.composition``) this app runs in a dual-config
    hybrid mode:
      - log lines come from the backend's LogDriver via the QtEventBridge
        (``attach_backend_ui``) instead of the legacy QFileSystemWatcher
        log reader,
      - the legacy 'spells' parser window is NOT built — the new
        ``SpellTimerWindow`` replaces it,
      - maps + discord windows keep running off the legacy
        ``nparse.config.json`` while the new pydantic Settings drives the
        backend. The maps UI gets rebuilt (and the legacy config retired)
        next milestone.
    """

    def __init__(self, *args, backend=None):
        super().__init__(*args)

        # New-core backend (None = pure legacy mode).
        self._backend = backend
        self._bridge = None
        self._spell_window = None
        self._save_new_settings = None
        self._backend_windows = {}

        # Updates
        self._toggled = False
        self._log_reader = None

        # Load Signals
        self._signals = {}
        self._signals["logreader"] = LogReaderSignals()
        self._signals["settings"] = SettingsSignals()
        self._signals["maps"] = MapsSignals()
        self._signals["locationsharing"] = LocationSharingSignals()

        # Load Services
        self._services = {}
        self._services["locationsharing"] = LocationSharingService()

        # Load Parsers
        self._load_parsers()
        self._settings = SettingsWindow()

        # Tray Icon
        self._system_tray = QSystemTrayIcon()
        self._system_tray.setIcon(QIcon(resource_path("data/ui/icon.png")))
        self._system_tray.setToolTip("nParse")
        # self._system_tray.setContextMenu(self._create_menu())
        self._system_tray.activated.connect(self._menu)
        self._system_tray.show()

        # Turn On
        self._toggle()

        if self.new_version_available():
            self._system_tray.showMessage(
                "nParse Update",
                f"New version available!\nCurrent: {CURRENT_VERSION}\nOnline: {ONLINE_VERSION}",
                msecs=3000,
            )

    @property
    def maps_window(self):
        """The legacy Maps parser window (remote map dots attach to it)."""
        return self._parsers_dict.get("maps")

    def _load_parsers(self):
        # Backend mode: the legacy spells window is replaced by the new
        # SpellTimerWindow, so keep it out of the parser list entirely.
        self._parsers_dict = {"maps": Maps()}
        if self._backend is None:
            self._parsers_dict["spells"] = Spells()
        self._parsers_dict["discord"] = Discord()
        self._parsers = list(self._parsers_dict.values())

    def attach_backend_ui(self, bridge, spell_window, save_new_settings=None, windows=None):
        """Backend mode wiring (called by nparseplus.app.create_app):
        feed LineEvents from the Qt bridge into the legacy parse path and
        hook up the new overlay windows for the tray menu. ``windows`` is an
        ordered {label: window} dict of extra toggleable windows (each needs
        .toggle() and .isVisible())."""
        self._bridge = bridge
        self._spell_window = spell_window
        self._save_new_settings = save_new_settings
        self._backend_windows = dict(windows or {})
        bridge.event_received.connect(self._on_backend_event)

    def _on_backend_event(self, event):
        # Delivered on the GUI thread (queued signal). The legacy windows
        # expect (timestamp, text-without-timestamp) tuples.
        if self._toggled and isinstance(event, LineEvent):
            self._parse((event.timestamp, event.line))

    def _toggle(self):
        if self._backend is not None:
            # Backend mode: lines arrive via the bridge (attach_backend_ui);
            # _toggled just gates whether they reach the legacy windows.
            self._toggled = not self._toggled
            return
        if not self._toggled:
            try:
                config.verify_paths()
            except ValueError as error:
                self._system_tray.showMessage(error.args[0], error.args[1], msecs=3000)

            else:
                self._log_reader = logreader.LogReader(
                    os.path.abspath(config.data["general"]["eq_log_dir"])
                )
                QApplication.instance()._signals["logreader"].new_line.connect(self._parse)
                self._toggled = True
        else:
            if self._log_reader:
                self._log_reader.deleteLater()
                self._log_reader = None
            self._toggled = False

    def _parse(self, new_line):
        if new_line:
            timestamp, text = new_line  # (datetime, text)
            #  don't send parse to non toggled items, except maps.  always parse maps
            for parser in self._parsers:
                if text.startswith("toggle_clickthrough_%s" % parser.name):
                    config.data[parser.name]["clickthrough"] = not config.data[parser.name][
                        "clickthrough"
                    ]
                    config.save()
                    parser._set_flags()
                elif text.startswith("toggle_%s" % parser.name):
                    parser.toggle()
                elif config.data[parser.name]["toggled"] or parser.name == "maps":
                    parser.parse(timestamp, text)

    def _menu(self, event):
        """Returns a new QMenu for system tray."""
        menu = QMenu()
        menu.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        # check online for new version
        if self.new_version_available():
            new_version_text = f"Update Available: {ONLINE_VERSION}"
        else:
            new_version_text = f"Version: {CURRENT_VERSION}"

        check_version_action = menu.addAction(new_version_text)
        if self._backend is not None and getattr(self._backend, "sharing", None) is not None:
            sharing_status_action = menu.addAction(f"Sharing: {self._backend.sharing.status}")
            sharing_status_action.setEnabled(False)
        menu.addSeparator()
        get_eq_dir_action = menu.addAction("Select EQ Logs Directory")
        menu.addSeparator()

        parser_toggles = set()
        for parser in self._parsers:
            toggle = menu.addAction(parser.name.title())
            toggle.setCheckable(True)
            toggle.setChecked(config.data[parser.name]["toggled"])
            parser_toggles.add(toggle)

        spell_timers_action = None
        if self._spell_window is not None:
            spell_timers_action = menu.addAction("Spell Timers")
            spell_timers_action.setCheckable(True)
            spell_timers_action.setChecked(self._spell_window.isVisible())

        backend_window_actions = {}
        for label, window in self._backend_windows.items():
            window_action = menu.addAction(label)
            window_action.setCheckable(True)
            window_action.setChecked(window.isVisible())
            backend_window_actions[window_action] = window

        menu.addSeparator()
        settings_action = menu.addAction("Settings")
        discord_conf_action = menu.addAction("Configure Discord")
        menu.addSeparator()
        quit_action = menu.addAction("Quit")

        action = menu.exec(QCursor.pos())

        if action == check_version_action:
            webbrowser.open("https://github.com/nomns/nparse/releases")

        elif action == get_eq_dir_action:
            dir_path = str(
                QFileDialog.getExistingDirectory(None, "Select Everquest Logs Directory")
            )
            if dir_path:
                config.data["general"]["eq_log_dir"] = dir_path
                config.save()
                if self._backend is not None:
                    # Point the new-core log driver at the directory and
                    # persist it to the new Settings as well.
                    self._backend.driver.set_log_dir(Path(dir_path))
                    self._backend.settings.general.eq_log_dir = Path(dir_path)
                    if self._save_new_settings is not None:
                        self._save_new_settings()
                else:
                    self._toggle()

        elif spell_timers_action is not None and action == spell_timers_action:
            self._spell_window.toggle()

        elif action in backend_window_actions:
            backend_window_actions[action].toggle()

        elif action == settings_action:
            self._settings._set_values()
            self._settings.exec()

        elif action == discord_conf_action:
            self._parsers_dict["discord"].show_settings()

        elif action == quit_action:
            if self._toggled:
                self._toggle()

            self._system_tray.setVisible(False)
            config.APP_EXIT = True
            self.quit()

        elif action in parser_toggles:
            parser = [parser for parser in self._parsers if parser.name == action.text().lower()][0]
            parser.toggle()

    def new_version_available(self):
        try:
            return ONLINE_VERSION > CURRENT_VERSION
        except:
            return False
