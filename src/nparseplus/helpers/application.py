import threading
import webbrowser
from pathlib import Path

from packaging.version import Version
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QCursor, QIcon
from PySide6.QtWidgets import QApplication, QFileDialog, QMenu, QSystemTrayIcon

from nparseplus import updater
from nparseplus.core.events import LineEvent
from nparseplus.helpers import config, resource_path
from nparseplus.helpers.settings import SettingsSignals
from nparseplus.parsers.discord import Discord
from nparseplus.parsers.maps import Maps
from nparseplus.ui import appquit
from nparseplus.ui.updatewindow import UpdateAvailableDialog

config.load("nparse.config.json")
# validate settings file
config.verify_settings()

import nparseplus

CURRENT_VERSION = Version(nparseplus.__version__)
UPDATE_CHECK_DELAY_MS = 10_000  # don't block or race startup


class NomnsParse(QApplication):
    """Application Control.

    Runs the app in backend mode: the Qt-free core
    (``nparseplus.composition``) drives everything and this legacy
    ``QApplication`` hosts the tray menu plus the still-legacy maps + discord
    windows:
      - log lines come from the backend's LogDriver via the QtEventBridge
        (``attach_backend_ui``),
      - spell timers are the new ``SpellTimerWindow`` (not a legacy parser),
      - maps + discord windows keep running off the legacy
        ``nparse.config.json`` while the new pydantic Settings drives the
        backend. The maps UI gets rebuilt (and the legacy config retired)
        next milestone.
    """

    update_available = Signal(object)  # ReleaseInfo, emitted off-thread
    update_checked = Signal(object)  # ReleaseInfo | None — manual tray-check result

    def __init__(self, *args, backend):
        super().__init__(*args)

        # The Qt-free core (composition.build_backend); always present now —
        # the app runs backend mode exclusively.
        self._backend = backend
        self._bridge = None
        self._spell_window = None
        self._save_new_settings = None
        self._backend_windows = {}
        self._window_layouts = None
        self._available_release = None
        self._update_window = None
        self.update_available.connect(self._on_update_available)
        self.update_checked.connect(self._on_update_checked)

        self._toggled = False

        # Load Signals
        self._signals = {}
        self._signals["settings"] = SettingsSignals()
        # (location sharing moved to the backend: core.sharing + net/)

        # Load Parsers
        self._load_parsers()

        # Tray Icon
        self._system_tray = QSystemTrayIcon()
        self._system_tray.setIcon(QIcon(resource_path("data/ui/icon.png")))
        self._system_tray.setToolTip("nParse")
        # self._system_tray.setContextMenu(self._create_menu())
        self._system_tray.activated.connect(self._menu)
        self._system_tray.show()

        # Turn On
        self._toggle()

        if self._update_check_enabled():
            QTimer.singleShot(UPDATE_CHECK_DELAY_MS, self._start_update_check)

    def _update_check_enabled(self):
        return bool(self._backend.settings.general.update_check)

    def _start_update_check(self):
        def work():
            release = updater.check_for_update()
            if release is not None:
                self.update_available.emit(release)

        threading.Thread(target=work, name="update-check", daemon=True).start()

    def _start_manual_update_check(self):
        """Tray 'Check for updates' — run the check now and report either way
        (the startup check above is silent when already up to date)."""

        def work():
            self.update_checked.emit(updater.check_for_update())

        threading.Thread(target=work, name="update-check-manual", daemon=True).start()

    def _on_update_checked(self, release):
        if release is not None:
            self._on_update_available(release)
        else:
            self._system_tray.showMessage(
                "nParse+ update",
                f"You're on the latest version ({CURRENT_VERSION}).",
                msecs=4000,
            )

    def _on_update_available(self, release):
        self._available_release = release
        self._system_tray.showMessage(
            "nParse+ update",
            f"Version {release.version} is available (installed: {CURRENT_VERSION}).\n"
            "Install it from the tray menu.",
            msecs=5000,
        )
        self._show_update_window()

    def _show_update_window(self):
        release = self._available_release
        if release is None:
            return
        window = self._update_window
        if window is None:
            window = UpdateAvailableDialog(release, str(CURRENT_VERSION))
            window.install_requested.connect(self._install_available_update)
            window.open_release_requested.connect(lambda: webbrowser.open(release.html_url))
            window.finished.connect(self._clear_update_window)
            self._update_window = window
        window.show()
        window.raise_()
        window.activateWindow()

    def _clear_update_window(self, _result=None):
        self._update_window = None

    def _install_available_update(self):
        release = self._available_release
        if release is None:
            return
        threading.Thread(
            target=updater.install_action,
            args=(release,),
            name="update-install",
            daemon=True,
        ).start()

    @property
    def maps_window(self):
        """The legacy Maps parser window (remote map dots attach to it)."""
        return self._parsers_dict.get("maps")

    def _load_parsers(self):
        # Spell timers are the new SpellTimerWindow; only the still-legacy maps
        # and discord parser windows live here.
        self._parsers_dict = {"maps": Maps(), "discord": Discord()}
        self._parsers = list(self._parsers_dict.values())

    def attach_backend_ui(
        self,
        bridge,
        spell_window,
        save_new_settings=None,
        windows=None,
        window_layouts=None,
    ):
        """Backend mode wiring (called by nparseplus.app.create_app):
        feed LineEvents from the Qt bridge into the legacy parse path and
        hook up the new overlay windows for the tray menu. ``windows`` is an
        ordered {label: window} dict of extra toggleable windows (each needs
        .toggle() and .isVisible())."""
        self._bridge = bridge
        self._spell_window = spell_window
        self._save_new_settings = save_new_settings
        self._backend_windows = dict(windows or {})
        self._window_layouts = window_layouts
        bridge.events_batch.connect(self._on_backend_events)

    def _on_backend_events(self, events):
        # Delivered on the GUI thread (one coalesced flush per bridge wake-up).
        # The legacy windows expect (timestamp, text-without-timestamp) tuples.
        if not self._toggled:
            return
        for event in events:
            if isinstance(event, LineEvent):
                self._parse((event.timestamp, event.line))

    def _on_backend_event(self, event):
        # Single-event compatibility path (tests / direct callers).
        self._on_backend_events([event])

    def _toggle(self):
        # Lines arrive via the Qt bridge (attach_backend_ui); _toggled just
        # gates whether they reach the legacy maps/discord windows.
        self._toggled = not self._toggled

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
                    # apply_window_state re-shows: setWindowFlags() alone
                    # would hide the window with no way to get it back.
                    parser.apply_window_state()
                elif text.startswith("toggle_%s" % parser.name):
                    parser.toggle()
                elif config.data[parser.name]["toggled"] or parser.name == "maps":
                    parser.parse(timestamp, text)

    def _build_tray_menu(self):
        """Construct the system-tray context menu without showing it.

        Returns ``(menu, actions)`` where ``actions`` maps the dispatch keys used
        by :meth:`_menu` to their ``QAction`` (or collection of them). Split out
        from ``_menu`` so the fully-populated menu can be built — and grabbed for
        a docs screenshot — without entering the blocking modal ``exec`` loop
        (``QMenu.exec`` can't be intercepted from Python).
        """
        menu = QMenu()
        menu.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        if self._available_release is not None:
            new_version_text = f"Install update v{self._available_release.version}"
        else:
            new_version_text = f"Version: {CURRENT_VERSION}"

        check_version_action = menu.addAction(new_version_text)
        check_now_action = menu.addAction("Check for updates")
        if getattr(self._backend, "sharing", None) is not None:
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

        if self._window_layouts is not None:
            menu.addSeparator()
            self._window_layouts.populate_menu(menu)

        menu.addSeparator()
        # (the unified "Settings" window arrives via _backend_windows)
        open_plugins_action = menu.addAction("Open Plugins Folder")
        discord_conf_action = menu.addAction("Configure Discord")
        menu.addSeparator()
        quit_action = menu.addAction("Quit")

        actions = {
            "check_version": check_version_action,
            "check_now": check_now_action,
            "get_eq_dir": get_eq_dir_action,
            "spell_timers": spell_timers_action,
            "backend_windows": backend_window_actions,
            "open_plugins": open_plugins_action,
            "discord_conf": discord_conf_action,
            "quit": quit_action,
            "parser_toggles": parser_toggles,
        }
        return menu, actions

    def _menu(self, event):
        """Show the system-tray context menu and act on the chosen entry."""
        menu, actions = self._build_tray_menu()

        action = menu.exec(QCursor.pos())

        if action == actions["check_version"]:
            if self._available_release is not None:
                self._show_update_window()
            else:
                webbrowser.open(updater.releases_page_url())

        elif action == actions["check_now"]:
            self._start_manual_update_check()

        elif action == actions["get_eq_dir"]:
            dir_path = str(
                QFileDialog.getExistingDirectory(None, "Select Everquest Logs Directory")
            )
            if dir_path:
                config.data["general"]["eq_log_dir"] = dir_path
                config.save()
                # Point the new-core log driver at the directory and persist it
                # to the new Settings as well.
                self._backend.driver.set_log_dir(Path(dir_path))
                self._backend.settings.general.eq_log_dir = Path(dir_path)
                if self._save_new_settings is not None:
                    self._save_new_settings()

        elif actions["spell_timers"] is not None and action == actions["spell_timers"]:
            self._spell_window.toggle()

        elif action in actions["backend_windows"]:
            actions["backend_windows"][action].toggle()

        elif action == actions["open_plugins"]:
            from PySide6.QtCore import QUrl
            from PySide6.QtGui import QDesktopServices

            from nparseplus.config.paths import ensure_plugins_dir

            QDesktopServices.openUrl(QUrl.fromLocalFile(str(ensure_plugins_dir())))

        elif action == actions["discord_conf"]:
            self._parsers_dict["discord"].show_settings()

        elif action == actions["quit"]:
            if self._toggled:
                self._toggle()

            self._system_tray.setVisible(False)
            config.APP_EXIT = True
            appquit.mark_quitting()  # new-core windows skip their shown=False clobber
            self.quit()

        elif action in actions["parser_toggles"]:
            parser = [parser for parser in self._parsers if parser.name == action.text().lower()][0]
            parser.toggle()
