"""The unified "nParse+ Settings" window — ONE settings surface.

Replaces both the legacy ``helpers.settings.SettingsWindow`` QDialog (which
edited the legacy ``config.data`` dict for the maps/discord windows) and the
M2 ``PreferencesWindow`` (which edited the Pydantic ``Settings``). Until the
maps window is rebuilt the app still runs two config systems, so this window
is the dual-write bridge: Apply writes the Pydantic model AND the legacy
dict, then notifies both worlds (``on_save`` / ``config.save`` +
``config_updated``, which live-applies legacy window opacity/flags) and
repaints the maps canvas (it reads its appearance keys at paint time).

Everything external is injected (legacy dict, save/notify/repaint callables,
window handles, backend player, zone database) so tests drive it with fakes.
"""

from __future__ import annotations

import contextlib
import logging
import threading
from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, ClassVar

from PySide6.QtCore import QSignalBlocker, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

import nparseplus
from nparseplus import updater
from nparseplus.audio.tts import default_speaker, list_voices
from nparseplus.config.settings import PlayerInfo, Settings, WindowState
from nparseplus.core import dps as dps_engine
from nparseplus.core import eqprocess, friends, visionfix
from nparseplus.core.enums import PlayerClass
from nparseplus.core.events import (
    AfterPlayerChangedEvent,
    ClassDetectedEvent,
    PlayerLevelDetectionEvent,
    WhoPlayerEvent,
    YouZonedEvent,
)
from nparseplus.core.player import TRACKABLE_CLASSES, ActivePlayer
from nparseplus.core.socialsync import SocialSyncWatcher
from nparseplus.core.zones import ZoneDatabase

# The one legacy import here, and it is a vocabulary, not state: the maps
# window still reads config.data, and these are the values its pan_mode key
# accepts (see helpers/config.py). Everything else legacy stays injected.
from nparseplus.helpers.config import PAN_CTRL_DRAG, PAN_DRAG
from nparseplus.net.discordauth import DiscordAuthResult
from nparseplus.net.discordauth import login as discord_login
from nparseplus.ui import appicon, chrome, chromewidgets, skins
from nparseplus.ui.overlaybase import OverlayWindowBase
from nparseplus.ui.skinwidgets import SkinPreview

logger = logging.getLogger(__name__)

WINDOW_KEY = "settings"
DEFAULT_GEOMETRY = (240, 160, 640, 560)

#: Shown under the mark in General's header. The same sentence the Flatpak
#: metainfo and the social card use, so the app describes itself identically
#: everywhere it is asked.
BRAND_TAGLINE = "EverQuest Project 1999 companion overlay"

#: How small the user is allowed to make this window.
#:
#: A floor on *legibility*, not on content: every page scrolls, and every form
#: row drops its field under its label, so nothing here is unreachable at this
#: size — it is small enough to park beside EverQuest and wide enough that the
#: sidebar plus one field still reads. Deliberately a plain number rather than
#: a multiple of the font size: the pages scroll, so a larger font makes this
#: window scroll sooner, not refuse to shrink.
#:
#: Not an absolute promise — Qt takes the LARGER of this and the layout's own
#: minimum. At a big font size the sidebar and the two buttons under the pages
#: want more than this, since their text cannot be narrowed, and how much more
#: depends on the platform's font. That floor is honest. The one worth
#: guarding against is a *page* setting it, which is what the tests assert.
MIN_SIZE = (420, 320)

# The Windows-grid rows. Legacy rows live in config.data[section]; new rows
# live in Settings.windows[key]. Both kinds get apply_window_state() called
# directly on their handle when applied.
LEGACY_WINDOW_ROWS = [("Maps", "maps"), ("Discord", "discord")]
NEW_WINDOW_ROWS = [
    ("Spell Timers", "spells"),
    ("DPS Meter", "dps"),
    ("Mob Info", "mobinfo"),
    ("Console", "console"),
    ("Trigger Editor", "triggereditor"),
    ("Macro Editor", "macroeditor"),
]
# Plugin rows are not listed here: they are passed in per session (see the
# `plugin_windows` kwarg), because only windows a plugin actually opened this
# launch get a row.
PLUGIN_WINDOWS_SECTION = "Plugin windows"
# Row labels are plugin-supplied, so cap them — a long title would squeeze the
# opacity slider out of the grid. The full text lives in the row's tooltip.
LABEL_LIMIT = 60


def elide(text: str, limit: int = LABEL_LIMIT) -> str:
    """Truncate `text` to `limit` characters, ellipsis included."""
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


# Class combo entries: every playable class (no OTHER), EQTool SettingsGeneral.
PLAYER_CLASSES = [cls for cls in PlayerClass if cls is not PlayerClass.OTHER]


#: Alert text sizes offered on the Appearance page (label, px).
OVERLAY_TEXT_SIZES = (("Small", 22), ("Medium", 28), ("Large", 32), ("Huge", 42))
#: Alert emphasis choices (label, stored value) — see GeneralSettings.
ALERT_EMPHASIS = (("Plain", "plain"), ("Pulse", "pulse"), ("Pulse + glow", "glow"))


class _SkinChoice(QFrame):
    """One clickable skin card: a live thumbnail over the skin's name.

    Selection is the card's own border, so the three sit side by side and the
    picked one is obvious without a radio column beside them.
    """

    def __init__(self, skin: skins.Skin, on_pick: Callable[[str], None], parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName(chrome.CARD)
        self.skin_name = skin.name
        self._on_pick = on_pick
        self._selected = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(skin.blurb)
        preview = SkinPreview(skin, self)
        preview.setFixedHeight(48)
        label = QLabel(skin.label, self)
        label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 4)
        layout.setSpacing(5)
        layout.addWidget(preview, 1)
        layout.addWidget(label, 0)
        self.set_selected(False)

    def set_selected(self, selected: bool) -> None:
        # A property the chrome sheet keys off rather than an inline border, so
        # the picked card's edge is the active skin's accent — the card that
        # previews a skin now also wears it.
        self._selected = selected
        self.setProperty(chrome.PROP_SELECTED, selected)
        chromewidgets.repolish(self)

    def is_selected(self) -> bool:
        return self._selected

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._on_pick(self.skin_name)
            event.accept()
            return
        super().mousePressEvent(event)


class SettingsPageSpec:
    """An externally contributed settings page (plugin manager, plugin pages).

    Duck-type-compatible with the SDK's ``PluginSettingsPageSpec``: ``title``,
    ``builder(parent) -> QWidget``, optional ``apply(widget)``. Builder and
    apply failures are isolated per page — a broken contribution never takes
    down the settings window or the built-in Apply flow.
    """

    def __init__(
        self,
        title: str,
        builder: Callable[[QWidget], QWidget],
        apply: Callable[[QWidget], None] | None = None,
    ) -> None:
        self.title = title
        self.builder = builder
        self.apply = apply


class _DirPicker(QWidget):
    def __init__(self, caption: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._caption = caption
        self.edit = QLineEdit(self)
        button = QPushButton("…", self)
        button.setFixedWidth(28)
        button.clicked.connect(self._browse)
        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.edit, 1)
        layout.addWidget(button, 0)
        self.setLayout(layout)

    def _browse(self) -> None:
        path = QFileDialog.getExistingDirectory(self, self._caption, self.edit.text())
        if path:
            self.edit.setText(path)

    def path(self) -> str:
        return self.edit.text().strip()


class _WindowRow:
    """One Windows-grid row: on-top checkbox + opacity slider (+clickthrough)."""

    def __init__(
        self,
        label: str,
        *,
        on_top: bool,
        opacity_pct: int,
        clickthrough: bool | None = None,
        handle: object | None = None,
        tooltip: str | None = None,
    ) -> None:
        self.label = label
        self.tooltip = tooltip
        self.handle = handle
        self.on_top = QCheckBox()
        self.on_top.setChecked(on_top)
        self.opacity = QSlider(Qt.Orientation.Horizontal)
        self.opacity.setRange(10, 100)  # 10% floor: a window must stay findable
        self.opacity.setValue(max(10, min(100, opacity_pct)))
        self.opacity.valueChanged.connect(self._live_preview)
        self.clickthrough: QCheckBox | None = None
        if clickthrough is not None:
            self.clickthrough = QCheckBox()
            self.clickthrough.setChecked(clickthrough)

    def _live_preview(self, value: int) -> None:
        if self.handle is not None:
            # A fake/absent handle must never break the slider.
            with contextlib.suppress(Exception):
                self.handle.setWindowOpacity(value / 100)


class UnifiedSettingsWindow(chromewidgets.ChromeMixin, OverlayWindowBase):
    # Emitted from the login worker thread; queued onto the GUI thread.
    _discord_auth_done = Signal(object)
    # Emitted from the update-check worker thread; carries a ReleaseInfo or
    # None (up to date / offline), queued onto the GUI thread.
    _update_status_ready = Signal(object)

    def __init__(
        self,
        settings: Settings,
        on_save: Callable[[], None],
        *,
        discord_login_fn: Callable[[], DiscordAuthResult | None] = discord_login,
        on_log_dir_changed: Callable[[Path], None] | None = None,
        on_audio_changed: Callable[[], None] | None = None,
        on_appearance_changed: Callable[[], None] | None = None,
        on_dps_changed: Callable[[], None] | None = None,
        on_mobinfo_changed: Callable[[], None] | None = None,
        on_overlay_timing_changed: Callable[[], None] | None = None,
        on_sharing_changed: Callable[[], None] | None = None,
        on_upload_target_changed: Callable[[], None] | None = None,
        on_install_dir_changed: Callable[[], object] | None = None,
        legacy_config: dict[str, Any] | None = None,
        on_legacy_save: Callable[[], None] | None = None,
        notify_legacy: Callable[[], None] | None = None,
        repaint_maps: Callable[[], None] | None = None,
        window_handles: dict[str, object] | None = None,
        plugin_windows: Sequence[tuple[str, str, object]] | None = None,
        backend_player: ActivePlayer | None = None,
        zones: ZoneDatabase | None = None,
        socials_sync: SocialSyncWatcher | None = None,
        extra_pages: Sequence[Any] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(
            settings=settings,
            window_key=WINDOW_KEY,
            title="nParse+ Settings",
            default_geometry=DEFAULT_GEOMETRY,
            on_save=on_save,
            default_state=WindowState(frameless=False, always_on_top=False),
            translucent=False,
            parent=parent,
        )
        self._on_log_dir_changed = on_log_dir_changed
        self._on_audio_changed = on_audio_changed
        self._on_appearance_changed = on_appearance_changed
        self._on_dps_changed = on_dps_changed
        # Turning the Mob Info wiki lookup on mid-session has to build the
        # client and the worker thread it needs, like the dump destination
        # below — the handler reads the setting live, the plumbing does not
        # exist until something makes it (#113).
        self._on_mobinfo_changed = on_mobinfo_changed
        # Overlay durations are BEHAVIOR, so they get their own callback
        # rather than riding on _on_appearance_changed — that one is also the
        # skin picker's preview path (#67).
        self._on_overlay_timing_changed = on_overlay_timing_changed
        self._on_sharing_changed = on_sharing_changed
        # Picking a dump destination has to bring its own network plumbing,
        # which sharing's callback above deliberately does not do (#68).
        self._on_upload_target_changed = on_upload_target_changed
        # The EQ install directory decides which spells_us.txt is live (#70).
        self._on_install_dir_changed = on_install_dir_changed
        # The skin the window opened with, so Close can undo a live preview.
        self._skin_on_open = settings.general.skin
        self._legacy = legacy_config if legacy_config is not None else {}
        self._on_legacy_save = on_legacy_save
        self._notify_legacy = notify_legacy
        self._repaint_maps = repaint_maps
        self._handles = window_handles or {}
        # (label, window key, widget) per plugin window built this session.
        # Deliberately plain tuples: create_app builds this window on every
        # launch, plugins on or off, so nothing plugin-shaped may be imported
        # here (tests/core/plugins/test_master_toggle.py pins that).
        self._plugin_windows = tuple(plugin_windows or ())
        self._backend_player = backend_player
        self._zones = zones
        self._socials_sync = socials_sync
        self._discord_login = discord_login_fn
        self._discord_auth_done.connect(self._finish_discord_login)
        self._update_status_ready.connect(self._on_update_status_ready)
        self._update_checking = False

        self._sidebar = QListWidget(self)
        self._sidebar.setFixedWidth(140)
        self._stack = QStackedWidget(self)
        self._sidebar.currentRowChanged.connect(self._stack.setCurrentIndex)

        for name, builder in (
            ("General", self._build_general),
            ("Appearance", self._build_appearance),
            ("Character", self._build_character),
            ("Friends", self._build_friends),
            ("Spell Timers", self._build_spell_timers),
            ("DPS Meter", self._build_dps),
            ("Maps", self._build_maps),
            ("Windows", self._build_windows_grid),
            ("Audio && Overlays", self._build_audio_overlays),
            ("Sharing", self._build_sharing),
            ("Advanced", self._build_advanced),
        ):
            self._sidebar.addItem(name.replace("&&", "&"))
            self._stack.addWidget(builder())

        # Externally contributed pages (the Plugins manager, plugin settings
        # pages). Each page is built and applied under its own guard.
        self._extra_pages: list[tuple[Any, QWidget]] = []
        for spec in extra_pages or ():
            try:
                page = spec.builder(self)
            except Exception:
                logger.exception("settings page %r failed to build", spec.title)
                page = QLabel("This page failed to build — see nparseplus.log.", self)
            self._sidebar.addItem(spec.title)
            # Scrolled like the built-in pages: the window's minimum size is
            # not a contributed page's to raise, and the widest page in the
            # app is a contributed one — the Plugins manager's table of
            # installed add-ons wants ~1800px, which would pin the window
            # wide open for exactly the users who enabled plugins.
            #
            # The wrapper goes in the stack; `_extra_pages` keeps what the
            # builder returned, so ``spec.apply`` still gets the widget it
            # made rather than a QScrollArea it has never heard of.
            self._stack.addWidget(self._scrollable(page))
            self._extra_pages.append((spec, page))
        self._sidebar.setCurrentRow(0)

        apply_button = QPushButton("Apply && Save", self)
        apply_button.clicked.connect(self.apply)
        close_button = QPushButton("Close", self)
        # Not plain hide(): the skin picker previews live, so closing without
        # applying has to put the windows back the way they were.
        close_button.clicked.connect(self._close_discarding_preview)

        body = QHBoxLayout()
        body.addWidget(self._sidebar)
        body.addWidget(self._stack, 1)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(apply_button)
        buttons.addWidget(close_button)
        layout = QVBoxLayout()
        layout.addLayout(body, 1)
        layout.addLayout(buttons)
        self.setLayout(layout)

        apply_button.setObjectName(chrome.PRIMARY)
        self._sidebar.setObjectName(chrome.SIDEBAR)
        self._let_rows_wrap()
        self.setMinimumSize(*MIN_SIZE)
        # Last: the pages must exist before the sheet reaches them.
        self.apply_chrome()
        self.restore_visibility()

    def _let_rows_wrap(self) -> None:
        """Every form row in the window puts its field under its label when
        the row will not fit.

        The other half of :meth:`_scrollable`, and the one that actually keeps
        the pages readable: a scroll area alone would let a narrow window
        scroll sideways past the labels, which is a miserable way to read a
        settings page. With this, a row's width floor is the wider of label
        and field instead of their sum, so narrowing reflows before it scrolls.

        Swept over every ``QFormLayout`` in one place rather than set on each
        as it is built — including the ones nested inside group boxes, which
        are the widest rows here — so a page added later cannot forget it.
        Extra pages contributed by plugins get it too, which is the intent:
        the window's minimum size is not theirs to raise.
        """
        for form in self.findChildren(QFormLayout):
            form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)

    # -- legacy dict access -----------------------------------------------------

    def _lc(self, section: str, key: str, default: Any) -> Any:
        return self._legacy.get(section, {}).get(key, default)

    def _lc_set(self, section: str, key: str, value: Any) -> None:
        self._legacy.setdefault(section, {})[key] = value

    # -- General ------------------------------------------------------------------

    def _build_general(self) -> QWidget:
        general = self._settings.general
        form = QFormLayout()
        self._log_dir = _DirPicker("Select EverQuest Logs directory", self)
        self._log_dir.edit.setText(str(general.eq_log_dir))
        form.addRow("EQ Logs directory", self._log_dir)
        self._install_dir = _DirPicker("Select EverQuest install directory", self)
        self._install_dir.edit.setText(str(general.eq_install_dir or ""))
        self._install_dir.edit.textChanged.connect(lambda _text: self._refresh_visionfix_status())
        form.addRow("EQ install directory", self._install_dir)
        self._update_check = QCheckBox(self)
        self._update_check.setChecked(general.update_check)
        form.addRow("Check for updates", self._update_check)
        form.addRow("Version", self._build_version_indicator())
        # Theme and font size live on the Appearance page now (with the skin
        # picker) — they answer "how does nParse+ look", not "how is it set up".
        # There is no hint row here any more either: "TTS and overlay durations
        # apply after restart" was untrue for TTS from 1.9 (the shared speaker
        # live-swaps on Apply) and is untrue for the durations now too (#67).
        # A stale restart note is worse than none — it teaches the user to
        # restart for nothing.
        return self._page(form, header=self._brand_header())

    def _brand_header(self) -> QWidget:
        """The mark, the name and the tagline, above General's first field.

        The one place inside the app that shows the icon at a size you can
        actually look at — everywhere else it is 16-32px in a tray or a title
        bar. Deliberately just three labels: this is a settings page, not a
        splash screen.
        """
        mark = QLabel(self)
        mark.setPixmap(appicon.app_pixmap(48))
        mark.setFixedSize(48, 48)

        name = QLabel("nParse+", self)
        name.setObjectName(chrome.TITLE)

        text = QVBoxLayout()
        text.setSpacing(0)
        text.addWidget(name)
        text.addWidget(chromewidgets.hint(BRAND_TAGLINE, self))

        row = QHBoxLayout()
        row.setSpacing(12)
        row.addWidget(mark, 0, Qt.AlignmentFlag.AlignTop)
        row.addLayout(text, 1)

        header = QWidget(self)
        header.setLayout(row)
        return header

    # -- Appearance ----------------------------------------------------------------

    def _build_appearance(self) -> QWidget:
        """Skin, theme and the on-game alert's look, in one place.

        The skin and the base font size both live here: they are the two
        controls that answer "how does nParse+ look", and splitting them
        across pages made that a scavenger hunt.
        """
        general = self._settings.general
        outer = QVBoxLayout()

        outer.addWidget(self._section_caption("OVERLAY SKIN"))
        self._skin_choices: list[_SkinChoice] = []
        cards = QHBoxLayout()
        cards.setSpacing(8)
        for name in skins.SKIN_ORDER:
            card = _SkinChoice(skins.SKINS[name], self._preview_skin, self)
            cards.addWidget(card, 1)
            self._skin_choices.append(card)
        outer.addLayout(cards)
        self._select_skin_card(general.skin)

        form = QFormLayout()
        # The base size for every window and overlay. Named to separate it from
        # the alert headline below, which is the one thing it does NOT drive.
        self._font_size = QSpinBox(self)
        self._font_size.setRange(6, 32)
        self._font_size.setValue(general.font_size)
        self._font_size.setToolTip(
            "Base type size for the overlays and this window. Applies live to "
            "open overlays; it does not change the big event-alert headline."
        )
        form.addRow("UI / overlay font size", self._font_size)

        self._overlay_text_size = QComboBox(self)
        for label, size in OVERLAY_TEXT_SIZES:
            self._overlay_text_size.addItem(f"{label} ({size}px)", size)
        if self._overlay_text_size.findData(general.overlay_text_size) < 0:
            self._overlay_text_size.addItem(
                f"Custom ({general.overlay_text_size}px)", general.overlay_text_size
            )
        self._overlay_text_size.setCurrentIndex(
            max(self._overlay_text_size.findData(general.overlay_text_size), 0)
        )
        self._overlay_text_size.setToolTip(
            "Size of the big word in an on-game event alert only — everything "
            "else follows the UI / overlay font size above."
        )
        form.addRow("Alert headline size", self._overlay_text_size)

        self._alert_emphasis = QComboBox(self)
        for label, value in ALERT_EMPHASIS:
            self._alert_emphasis.addItem(label, value)
        self._alert_emphasis.setCurrentIndex(
            max(self._alert_emphasis.findData(general.alert_emphasis), 0)
        )
        self._alert_emphasis.setToolTip(
            "How hard an on-game alert pushes: a steady word, a slow pulse, "
            "or the pulse plus a colored halo behind it."
        )
        form.addRow("Alert emphasis", self._alert_emphasis)

        self._overlay_shadow = QCheckBox(self)
        self._overlay_shadow.setChecked(general.overlay_text_shadow)
        self._overlay_shadow.setToolTip(
            "Soft drop shadow behind overlay alert text. The blur re-renders "
            "on every repaint of the always-on-top overlay — turn it off if "
            "the overlay stutters."
        )
        form.addRow("Alert text shadow", self._overlay_shadow)

        self._frame_opacity = QSlider(Qt.Orientation.Horizontal, self)
        self._frame_opacity.setRange(20, 100)
        self._frame_opacity.setValue(general.frame_opacity)
        self._frame_opacity_label = QLabel(f"{general.frame_opacity}%", self)
        self._frame_opacity.valueChanged.connect(
            lambda value: self._frame_opacity_label.setText(f"{value}%")
        )
        opacity_row = QHBoxLayout()
        opacity_row.addWidget(self._frame_opacity, 1)
        opacity_row.addWidget(self._frame_opacity_label, 0)
        opacity_holder = QWidget(self)
        opacity_holder.setLayout(opacity_row)
        self._frame_opacity.setToolTip(
            "Fades only the skin's frame and glass. Countdowns, bars and icons "
            "stay at full contrast — unlike window opacity, which dims those too."
        )
        form.addRow("Frame opacity", opacity_holder)
        outer.addLayout(form)

        note = chromewidgets.hint(
            "Skin and font size apply live to every window and overlay.",
            self,
        )
        outer.addWidget(note)
        outer.addStretch(1)
        page = QWidget(self)
        page.setLayout(outer)
        return self._scrollable(page)

    def show_page(self, title: str) -> None:
        """Show the window with ``title``'s page selected (unknown = no-op
        beyond showing). Used by the tray's "Appearance…" entry."""
        items = self._sidebar.findItems(title, Qt.MatchFlag.MatchExactly)
        if items:
            self._sidebar.setCurrentRow(self._sidebar.row(items[0]))
        self.show()
        self.raise_()

    def _section_caption(self, text: str) -> QLabel:
        return chromewidgets.caption(text, self)

    def _select_skin_card(self, name: str) -> None:
        for card in self._skin_choices:
            card.set_selected(card.skin_name == name)

    def selected_skin(self) -> str:
        """The skin the picker currently shows as chosen (test/debug hook)."""
        for card in self._skin_choices:
            if card.is_selected():
                return card.skin_name
        return skins.DEFAULT_SKIN

    def _preview_skin(self, name: str) -> None:
        """Apply a clicked skin to the live windows straight away.

        A skin is a look; you judge it by looking at it, so the picker is the
        preview. The value is written into settings here so the appearance
        callback (shared with the tray) has one source of truth — Apply is
        what makes it durable, and Close puts it back (see
        ``_close_discarding_preview``).
        """
        self._select_skin_card(name)
        self._settings.general.skin = name  # type: ignore[assignment]
        if self._on_appearance_changed is not None:
            self._on_appearance_changed()

    def _close_discarding_preview(self) -> None:
        """Close, undoing any un-applied skin preview."""
        if self._settings.general.skin != self._skin_on_open:
            self._settings.general.skin = self._skin_on_open  # type: ignore[assignment]
            self._select_skin_card(self._skin_on_open)
            if self._on_appearance_changed is not None:
                self._on_appearance_changed()
        self.hide()

    # -- version / update indicator ------------------------------------------------

    def _build_version_indicator(self) -> QWidget:
        """The current version + an up-to-date / update-available status badge
        and a "Check now" button (the version was previously tray-only)."""
        row = QWidget(self)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self._version_label = QLabel(f"nParse+ {nparseplus.__version__}", self)
        layout.addWidget(self._version_label)
        self._update_badge = chromewidgets.badge(self)
        layout.addWidget(self._update_badge)
        layout.addStretch(1)
        self._update_check_button = QPushButton("Check now", self)
        self._update_check_button.clicked.connect(self._check_for_update_async)
        layout.addWidget(self._update_check_button)
        self._set_update_badge(None, checked=False)
        return row

    def _set_update_badge(self, release: object, *, checked: bool) -> None:
        """Tone the badge: blank before a check, green up-to-date, amber when a
        newer release is available.

        The tone is a property the chrome sheet keys off, not an inline style,
        so the pill follows a skin change and stays readable in the light
        theme — which the old hardcoded ``color: white`` did not.
        """
        if not checked:
            chromewidgets.set_badge(self._update_badge, "")
        elif release is None:
            chromewidgets.set_badge(self._update_badge, "Up to date", "ok")
        else:
            chromewidgets.set_badge(
                self._update_badge, f"Update available: v{release.version}", "warn"
            )

    def _check_for_update_async(self) -> None:
        if self._update_checking:
            return
        self._update_checking = True
        self._update_check_button.setEnabled(False)
        chromewidgets.set_badge(self._update_badge, "Checking…", "busy")

        def work() -> None:
            try:
                release = updater.check_for_update()
            except Exception:  # updater already fails soft, but never leak a thread crash
                release = None
            self._update_status_ready.emit(release)

        threading.Thread(target=work, name="settings-update-check", daemon=True).start()

    def _on_update_status_ready(self, release: object) -> None:
        self._update_checking = False
        self._update_check_button.setEnabled(True)
        self._set_update_badge(release, checked=True)

    # -- Character -------------------------------------------------------------------

    def _build_character(self) -> QWidget:
        form = QFormLayout()
        self._char_combo = QComboBox(self)
        for info in self._settings.players:
            self._char_combo.addItem(self._char_label(info))
        form.addRow("Character", self._char_combo)

        self._char_class = QComboBox(self)
        self._char_class.addItem("(unknown)")
        for cls in PLAYER_CLASSES:
            self._char_class.addItem(cls.display_name)
        self._char_class.currentIndexChanged.connect(lambda _i: self._sync_track_enabled())
        form.addRow("Class", self._char_class)

        self._char_level = QSpinBox(self)
        self._char_level.setRange(0, 60)
        self._char_level.setSpecialValueText("(unknown)")
        form.addRow("Your Level", self._char_level)

        self._char_zone = QComboBox(self)
        self._char_zone.setEditable(True)
        if self._zones is not None:
            self._char_zone.addItem("")
            for long_name in sorted(self._zones.long_names()):
                self._char_zone.addItem(long_name)
        form.addRow("Zone", self._char_zone)

        self._char_track = QSpinBox(self)
        self._char_track.setRange(0, 200)
        self._char_track.setSpecialValueText("(unset)")
        form.addRow("Track Skill", self._char_track)

        self._char_sharing = QComboBox(self)
        self._char_sharing.addItems(["everyone", "guild", "off"])
        form.addRow("Location sharing", self._char_sharing)

        self._char_share_timers = QCheckBox(self)
        form.addRow("Share timers", self._char_share_timers)

        # EQTool TimerRecastSetting: recast a detrimental spell on an NPC and
        # either refresh the running timer or stack a new one per cast (roots
        # always refresh).
        self._char_timer_recast = QComboBox(self)
        self._char_timer_recast.addItem("Restart Current Timer", "RestartCurrentTimer")
        self._char_timer_recast.addItem("Start New Timer", "StartNewTimer")
        self._char_timer_recast.setToolTip(
            "Recasting a detrimental spell on an NPC: restart the running timer, "
            "or start a new one per cast (for DoTs stacked on several mobs). "
            "Root spells always restart."
        )
        form.addRow("Timer recast", self._char_timer_recast)

        # Spell class filters (EQTool "Class Filters"): a spell shows on other
        # players when ANY checked class can cast it. All checked = show all.
        filters_box = QGroupBox("Show spells for classes", self)
        grid = QGridLayout()
        self._class_filter_boxes: dict[PlayerClass, QCheckBox] = {}
        for i, cls in enumerate(PLAYER_CLASSES):
            box = QCheckBox(cls.display_name, self)
            self._class_filter_boxes[cls] = box
            grid.addWidget(box, i // 3, i % 3)
        filters_box.setLayout(grid)
        form.addRow(filters_box)

        self._char_combo.currentIndexChanged.connect(lambda _i: self._load_character())
        self._active_character = self._backend_character()
        self._select_active_character()
        self._load_character()
        return self._page(form)

    @staticmethod
    def _char_label(info: PlayerInfo) -> str:
        return f"{info.name} ({info.server})"

    def _backend_character(self) -> tuple[str, str | None] | None:
        player = self._backend_player
        if player is None or not player.name:
            return None
        return (player.name, player.server_key)

    def _selected_player(self) -> PlayerInfo | None:
        index = self._char_combo.currentIndex()
        if 0 <= index < len(self._settings.players):
            return self._settings.players[index]
        return None

    def _select_active_character(self) -> None:
        active = self._backend_character()
        if active is None:
            return
        for i, info in enumerate(self._settings.players):
            if (info.name, info.server) == active:
                self._char_combo.setCurrentIndex(i)
                return

    def refresh_characters(self) -> None:
        """Re-sync the character combo with ``settings.players``.

        Profiles are created lazily on the driver thread once a log attaches,
        usually AFTER this window was built — so the combo must be refreshed
        on show and on character-change events. Repopulates only when the
        profile list or the active character actually changed, keeping any
        unsaved field edits for a still-selected character intact.
        """
        active = self._backend_character()
        active_changed = active is not None and active != self._active_character
        self._active_character = active
        labels = [self._char_label(info) for info in self._settings.players]
        current = [self._char_combo.itemText(i) for i in range(self._char_combo.count())]
        if labels == current and not active_changed:
            return
        previous_label = self._char_combo.currentText()
        blocker = QSignalBlocker(self._char_combo)
        self._char_combo.clear()
        self._char_combo.addItems(labels)
        index = -1
        if not active_changed and previous_label in labels:
            index = labels.index(previous_label)
        elif active is not None:
            for i, info in enumerate(self._settings.players):
                if (info.name, info.server) == active:
                    index = i
                    break
        if index < 0 and labels:
            index = 0
        self._char_combo.setCurrentIndex(index)
        del blocker
        if self._char_combo.currentText() != previous_label or not previous_label:
            self._load_character()

    def handle_backend_event(self, event: object) -> None:
        """Bridge slot (GUI thread): keep the selected active profile current."""
        # Early type filter: this slot sees the whole bus firehose (every log
        # line), but only five event types matter — bail before doing any
        # character-resolution work per event.
        if not isinstance(
            event,
            (
                AfterPlayerChangedEvent,
                WhoPlayerEvent,
                ClassDetectedEvent,
                PlayerLevelDetectionEvent,
                YouZonedEvent,
            ),
        ):
            return
        if isinstance(event, AfterPlayerChangedEvent):
            self.refresh_characters()
            return

        active = self._backend_character()
        if active is not None and active != self._active_character:
            # Stale bookkeeping (e.g. the profile was created after this
            # window was built and no character-change event re-synced us):
            # heal it now so live /who//zone updates aren't silently dropped.
            self.refresh_characters()
        info = self._selected_player()
        if info is None or active is None or (info.name, info.server) != active:
            return
        if isinstance(event, WhoPlayerEvent):
            if event.player.name.casefold() != info.name.casefold():
                return
            self._refresh_character_fields(
                player_class=event.player.player_class is not None,
                level=event.player.level is not None,
            )
        elif isinstance(event, ClassDetectedEvent):
            self._refresh_character_fields(player_class=True)
        elif isinstance(event, PlayerLevelDetectionEvent):
            self._refresh_character_fields(level=True)
        elif isinstance(event, YouZonedEvent):
            self._refresh_character_fields(zone=True)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        # Re-baseline the skin preview: the tray's UI Skin submenu can have
        # changed it while this window was hidden, and Close must revert to
        # what is live now, not to whatever was live at the last open.
        self._skin_on_open = self._settings.general.skin
        self._select_skin_card(self._skin_on_open)
        self.refresh_characters()
        # Always reload the character fields: the backend mutates the profile
        # (/who, level dings, zoning) while the window is hidden, and
        # refresh_characters skips _load_character when the combo is
        # unchanged — without this, reopening showed stale class/level/zone.
        self._load_character()

    def _load_character(self) -> None:
        info = self._selected_player()
        enabled = info is not None
        for widget in (
            self._char_class,
            self._char_level,
            self._char_zone,
            self._char_track,
            self._char_sharing,
            self._char_share_timers,
            self._char_timer_recast,
            *self._class_filter_boxes.values(),
        ):
            widget.setEnabled(enabled)
        if info is None:
            return
        self._char_class.setCurrentIndex(self._class_combo_index(info.player_class))
        self._char_level.setValue(info.level or 0)
        zone_display = info.zone
        if self._zones is not None and info.zone:
            zone_display = self._zones.long_name(info.zone) or info.zone
        self._char_zone.setCurrentText(zone_display)
        self._char_track.setValue(info.tracking_skill or 0)
        self._char_sharing.setCurrentText(info.map_location_sharing)
        self._char_share_timers.setChecked(info.share_timers)
        recast_index = self._char_timer_recast.findData(info.timer_recast)
        self._char_timer_recast.setCurrentIndex(max(recast_index, 0))
        selected = info.show_spells_for_classes
        for cls, box in self._class_filter_boxes.items():
            box.setChecked(selected is None or int(cls) in selected)
        self._sync_track_enabled()

    def _refresh_character_fields(
        self,
        *,
        player_class: bool = False,
        level: bool = False,
        zone: bool = False,
    ) -> None:
        """Refresh backend-owned fields without discarding other unsaved edits."""
        info = self._selected_player()
        if info is None:
            return
        if player_class:
            self._char_class.setCurrentIndex(self._class_combo_index(info.player_class))
            # The class signal enables Track Skill. Restore its saved value in
            # case the formerly-unknown class had caused the widget to clear.
            self._char_track.setValue(info.tracking_skill or 0)
            self._sync_track_enabled()
        if level:
            self._char_level.setValue(info.level or 0)
        if zone:
            zone_display = info.zone
            if self._zones is not None and info.zone:
                zone_display = self._zones.long_name(info.zone) or info.zone
            self._char_zone.setCurrentText(zone_display)

    @staticmethod
    def _class_combo_index(raw: int | None) -> int:
        """Class-combo index for a stored class value; 0 ("unknown") when the
        value is None, PlayerClass.OTHER (the castable-by-everyone spell
        fixup, not a real class), or junk from a hand-edited settings.json —
        PLAYER_CLASSES.index would raise for those in a live-event slot."""
        if raw is None:
            return 0
        try:
            cls = PlayerClass(raw)
        except ValueError:
            return 0
        if cls not in PLAYER_CLASSES:
            return 0
        return PLAYER_CLASSES.index(cls) + 1

    def _combo_class(self) -> PlayerClass | None:
        index = self._char_class.currentIndex()
        return PLAYER_CLASSES[index - 1] if index > 0 else None

    def _sync_track_enabled(self) -> None:
        """Track Skill only means something for Druid/Ranger/Bard (EQTool)."""
        trackable = self._combo_class() in TRACKABLE_CLASSES
        self._char_track.setEnabled(trackable and self._char_combo.currentIndex() >= 0)
        if not trackable:
            self._char_track.setValue(0)

    def _apply_character(self) -> None:
        info = self._selected_player()
        if info is None:
            return
        # Mutate IN PLACE: handlers and the sharing coordinator hold this object.
        cls = self._combo_class()
        info.player_class = int(cls) if cls is not None else None
        info.level = self._char_level.value() or None
        zone_text = self._char_zone.currentText().strip()
        if self._zones is not None and zone_text:
            info.zone = self._zones.short_name(zone_text) or zone_text
        else:
            info.zone = zone_text
        info.tracking_skill = self._char_track.value() if cls in TRACKABLE_CLASSES else 0
        info.map_location_sharing = self._char_sharing.currentText()  # type: ignore[assignment]
        info.share_timers = self._char_share_timers.isChecked()
        info.timer_recast = self._char_timer_recast.currentData()
        checked = [int(cls) for cls, box in self._class_filter_boxes.items() if box.isChecked()]
        info.show_spells_for_classes = None if len(checked) == len(PLAYER_CLASSES) else checked

        player = self._backend_player
        if player is not None and info.name == player.name and info.server == player.server_key:
            player.player_class = cls
            player.level = info.level
            if info.zone:
                player.zone = info.zone
            player.tracking_skill = info.tracking_skill or None

    # -- Friends (EQ client [Friends] ini sync, EQTool SettingsGeneral) -------------------

    def _build_friends(self) -> QWidget:
        layout = QVBoxLayout()
        form = QFormLayout()
        self._friends_server = QComboBox(self)
        self._friends_server.addItems(list(friends.SERVER_SUFFIXES))
        self._friends_server.currentIndexChanged.connect(lambda _i: self._load_friends())
        form.addRow("Server", self._friends_server)
        layout.addLayout(form)
        self._friends_text = QPlainTextEdit(self)
        self._friends_text.setPlaceholderText("One friend name per line…")
        layout.addWidget(self._friends_text, 1)
        buttons = QHBoxLayout()
        load_button = QPushButton("Load from characters", self)
        load_button.clicked.connect(self._load_friends)
        push_button = QPushButton("Push to all characters", self)
        push_button.clicked.connect(self._push_friends)
        buttons.addWidget(load_button)
        buttons.addWidget(push_button)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        self._friends_status = chromewidgets.hint(
            "Merges every character's in-game friends list on the selected server; "
            "Push writes the merged list back (originals backed up to friends_backup/).",
            self,
        )
        layout.addWidget(self._friends_status)
        page = QWidget(self)
        page.setLayout(layout)
        return self._scrollable(page)

    def _friends_files(self) -> list[Path]:
        eq_dir = self._install_dir.path() or str(self._settings.general.eq_install_dir or "")
        if not eq_dir:
            return []
        suffix = friends.SERVER_SUFFIXES[self._friends_server.currentText()]
        return friends.friend_ini_files(Path(eq_dir), suffix)

    def _load_friends(self) -> None:
        files = self._friends_files()
        if not files:
            self._friends_status.setText(
                "No character ini files found — set the EQ install directory on General."
            )
            self._friends_text.setPlainText("")
            return
        merged = friends.merged_friends(files)
        self._friends_text.setPlainText("\n".join(merged))
        self._friends_status.setText(f"{len(merged)} friends across {len(files)} character(s).")

    def _push_friends(self) -> None:
        files = self._friends_files()
        if not files:
            self._friends_status.setText("No character ini files found for this server.")
            return
        names = self._friends_text.toPlainText().splitlines()
        errors = friends.push_friends(files, names)
        if errors:
            self._friends_status.setText("Some files failed: " + "; ".join(errors))
        else:
            self._friends_status.setText(
                f"Pushed {len(friends.normalize_names(names))} friends to {len(files)} file(s)."
            )

    # -- Spell Timers -------------------------------------------------------------------

    def _build_spell_timers(self) -> QWidget:
        spellwindow = self._settings.spellwindow
        form = QFormLayout()
        self._row_sort_combo = QComboBox(self)
        self._row_sort_combo.addItem("Time remaining", "time_remaining")
        self._row_sort_combo.addItem("Alphabetical", "alphabetical")
        self._row_sort_combo.setCurrentIndex(
            max(self._row_sort_combo.findData(spellwindow.row_sort), 0)
        )
        self._row_sort_combo.setToolTip(
            "Order rows under each header by soonest-to-expire (default) or by name."
        )
        form.addRow("Sort timers by", self._row_sort_combo)
        self._you_only = QCheckBox(self)
        self._you_only.setChecked(spellwindow.you_only_spells)
        form.addRow("Show only your own spells", self._you_only)
        self._show_rolls = QCheckBox(self)
        self._show_rolls.setChecked(spellwindow.show_random_rolls)
        form.addRow("Show random rolls", self._show_rolls)
        self._bar_fade = QCheckBox(self)
        self._bar_fade.setChecked(spellwindow.bar_fade_to_red)
        self._bar_fade.setToolTip(
            "Progress bars shift from their normal color toward red as the "
            "timer runs down. Boat and roll timers keep their color."
        )
        form.addRow("Fade timer bars to red", self._bar_fade)
        # Category display toggles (hide the section; timers keep running
        # and respawn-expiry audio still fires while hidden).
        self._show_boats = QCheckBox(self)
        self._show_boats.setChecked(spellwindow.show_boats)
        form.addRow("Show boat timers", self._show_boats)
        self._show_mob_timers = QCheckBox(self)
        self._show_mob_timers.setChecked(spellwindow.show_mob_timers)
        self._show_mob_timers.setToolTip(
            "The Mob Timers section: mob respawn/Sirran countdowns and FTE raid rules."
        )
        form.addRow("Show mob timers", self._show_mob_timers)
        self._show_roll_timers = QCheckBox(self)
        self._show_roll_timers.setChecked(spellwindow.show_roll_timers)
        self._show_roll_timers.setToolTip(
            "The Roll Timers section: Ring 8 and Scout Charisa server roll windows."
        )
        form.addRow("Show roll timers", self._show_roll_timers)
        self._show_custom_timers = QCheckBox(self)
        self._show_custom_timers.setChecked(spellwindow.show_custom_timers)
        self._show_custom_timers.setToolTip(
            "The Custom Timers section: countdowns from triggers, chat commands, "
            "and shared remote timers."
        )
        form.addRow("Show custom timers", self._show_custom_timers)
        self._raid_group_by_spell = QCheckBox(self)
        self._raid_group_by_spell.setChecked(spellwindow.raid_group_by_spell)
        self._raid_group_by_spell.setToolTip(
            "Raid mode: when the buffs you cast on other players cover more "
            "targets than distinct spells, group them by spell (the spell heads, "
            "targets list). Off by default; targets always stay the headers."
        )
        form.addRow("Group buffs by spell (raid mode)", self._raid_group_by_spell)
        self._best_guess = QCheckBox(self)
        self._best_guess.setChecked(spellwindow.best_guess_spells)
        self._best_guess.setToolTip(
            "When a cast message matches several spells, start a timer for the "
            "closest-level guess. Off: ambiguous casts start no timer."
        )
        form.addRow("Guess ambiguous spells", self._best_guess)
        self._respawn_audio = QCheckBox(self)
        self._respawn_audio.setChecked(spellwindow.respawn_expiry_audio)
        self._respawn_audio.setToolTip(
            'Speak "<mob> spawn timer expired" when a respawn countdown runs out.'
        )
        form.addRow("Announce respawn-timer expiry", self._respawn_audio)
        self._buff_fade_secs = QSpinBox(self)
        self._buff_fade_secs.setRange(0, 300)
        self._buff_fade_secs.setSuffix(" s")
        self._buff_fade_secs.setSpecialValueText("off")
        self._buff_fade_secs.setValue(spellwindow.buff_fade_warning_seconds)
        self._buff_fade_secs.setToolTip(
            "Warn this many seconds before one of your own buffs fades "
            "(the time label turns red; optional voice alert below)."
        )
        form.addRow("Buff-fade warning", self._buff_fade_secs)
        self._buff_fade_audio = QCheckBox(self)
        self._buff_fade_audio.setChecked(spellwindow.buff_fade_warning_audio)
        form.addRow("Speak buff-fade warnings", self._buff_fade_audio)
        self._post_expiry_flash = QCheckBox(self)
        self._post_expiry_flash.setChecked(spellwindow.post_expiry_flash_enabled)
        self._post_expiry_flash.setToolTip(
            "Keep chosen spells on-screen after they expire, flashing as a "
            "rebuff/recast prompt (click the row to dismiss). Choose which "
            'spells with each row\'s right-click "Flash on expiry".'
        )
        form.addRow("Flash spells after expiry", self._post_expiry_flash)
        self._post_expiry_secs = QSpinBox(self)
        self._post_expiry_secs.setRange(1, 300)
        self._post_expiry_secs.setSuffix(" s")
        self._post_expiry_secs.setValue(spellwindow.post_expiry_flash_seconds)
        self._post_expiry_secs.setToolTip(
            "How long an expired spell keeps flashing before it drops."
        )
        form.addRow("Post-expiry flash time", self._post_expiry_secs)
        note = chromewidgets.hint("Per-class spell filters live on the Character page.", self)
        form.addRow(note)
        return self._page(form)

    # -- DPS meter (core.dps.FightTracker tunables; all apply live) ----------------------

    def _build_dps(self) -> QWidget:
        dps = self._settings.dps
        form = QFormLayout()

        self._dps_sources = QComboBox(self)
        for mode in dps_engine.DAMAGE_SOURCES:
            self._dps_sources.addItem(dps_engine.DAMAGE_SOURCE_LABELS[mode], mode)
        index = self._dps_sources.findData(dps.damage_sources)
        self._dps_sources.setCurrentIndex(max(0, index))
        self._dps_sources.setToolTip(
            'EverQuest logs spell, proc and damage-over-time damage as "<target> '
            'was hit by non-melee for N points", which names no attacker at '
            "all.\n\n"
            "Melee only — weapon and fist damage. A caster's row stays empty.\n\n"
            "Melee + my spells (default) — also counts non-melee damage that "
            "lands while you are casting or just after, which is the one "
            "signal your log gives. Another player nuking the same target in "
            "that moment can be miscredited to you; nothing in the line "
            "separates them.\n\n"
            "All damage — also counts non-melee damage that follows no cast "
            'of yours. It is listed under "(spell damage)" rather than '
            "credited to you, so the group percentages stay right.\n\n"
            "No mode can show damage over time: Project 1999 does not log "
            "DoT ticks."
        )
        form.addRow("Count damage from", self._dps_sources)

        self._dps_credit_window = QDoubleSpinBox(self)
        self._dps_credit_window.setRange(0.0, 30.0)
        self._dps_credit_window.setDecimals(1)
        self._dps_credit_window.setSingleStep(0.5)
        self._dps_credit_window.setSuffix(" s")
        self._dps_credit_window.setValue(dps.spell_credit_window_seconds)
        self._dps_credit_window.setToolTip(
            "How long after one of your casts a non-melee hit still counts as "
            "yours.\n\n"
            "Widen it if your nukes are being missed (a slow spell whose "
            "landing message nParse+ did not recognise); narrow it to be "
            "stricter about picking up other players' spell damage. Only used "
            "by the two modes above that count non-melee damage."
        )
        form.addRow("Spell credit window", self._dps_credit_window)

        self._dps_count_pet = QCheckBox(self)
        self._dps_count_pet.setChecked(dps.count_pet_damage)
        self._dps_count_pet.setToolTip(
            "Add your pet's damage to the session Best / Now / Last footer.\n\n"
            "Off by default: your pet is counted as its own attacker, the way "
            "every other row is. Turn this on if you read the pet as part of "
            "your own output — a magician or necromancer usually does.\n\n"
            "Your pet always gets its own row either way, marked (pet) and "
            "highlighted like yours; whether the pet is holding up is worth "
            "seeing on its own. This only decides whether your headline "
            "number is you, or you and your pet together."
        )
        form.addRow("Count pet damage as mine", self._dps_count_pet)

        self._dps_retention = QDoubleSpinBox(self)
        self._dps_retention.setRange(0.0, 3600.0)
        self._dps_retention.setDecimals(0)
        self._dps_retention.setSingleStep(10.0)
        self._dps_retention.setSuffix(" s")
        self._dps_retention.setSpecialValueText("never")
        self._dps_retention.setValue(dps.fight_retention_seconds)
        self._dps_retention.setToolTip(
            "How long a target's group stays on screen after the last hit "
            "against it from anyone.\n\n"
            "Individual attackers are never dropped: whoever has landed "
            "anything on a target stays listed for as long as that target's "
            "group is up, so an opener who stops swinging does not vanish "
            "mid-fight. The whole group retires together.\n\n"
            '"never" keeps groups until you zone, camp, or die.'
        )
        form.addRow("Attacker dropoff", self._dps_retention)

        self._dps_window = QDoubleSpinBox(self)
        self._dps_window.setRange(1.0, 300.0)
        self._dps_window.setDecimals(0)
        self._dps_window.setSuffix(" s")
        self._dps_window.setValue(dps.trailing_window_seconds)
        self._dps_window.setToolTip(
            'The span each row\'s "dps" number is averaged over (EQTool used '
            "12 s).\n\n"
            "Damage is always divided by the full window, never by how long "
            "the fight has actually run, so a burst reads low until the "
            "window fills: 400 damage two seconds in shows 33 dps at a 12 s "
            "window. Shorter reacts faster and swings harder; longer is "
            "steadier."
        )
        form.addRow("DPS averaging window", self._dps_window)

        self._dps_session_min = QDoubleSpinBox(self)
        self._dps_session_min.setRange(0.0, 600.0)
        self._dps_session_min.setDecimals(0)
        self._dps_session_min.setSuffix(" s")
        self._dps_session_min.setSpecialValueText("no minimum")
        self._dps_session_min.setValue(dps.session_min_fight_seconds)
        self._dps_session_min.setToolTip(
            "A fight must run longer than this before your row counts toward "
            "the Best / Now / Last footer (EQTool required 20 s).\n\n"
            "Most trash dies faster than 20 s, which is why that footer can "
            "sit at zero all session. Lower it to have short fights count."
        )
        form.addRow("Session stat minimum fight", self._dps_session_min)

        form.addRow(
            chromewidgets.hint(
                "These apply as soon as you hit Apply — no restart. Damage "
                "already counted is not recounted, so a change to what counts "
                "takes effect on the next hit; changing a rule that decides "
                "what the footer measures clears Best and Now.",
                self,
            )
        )
        return self._page(form)

    # -- Maps (legacy config keys until the maps window is rebuilt) ----------------------

    def _build_maps(self) -> QWidget:
        form = QFormLayout()
        self._maps_line_width = QSpinBox(self)
        self._maps_line_width.setRange(1, 10)
        self._maps_line_width.setValue(int(self._lc("maps", "line_width", 1)))
        form.addRow("Map line width", self._maps_line_width)
        self._maps_grid_width = QSpinBox(self)
        self._maps_grid_width.setRange(1, 10)
        self._maps_grid_width.setValue(int(self._lc("maps", "grid_line_width", 1)))
        form.addRow("Grid line width", self._maps_grid_width)
        self._maps_font_scale = QSpinBox(self)
        self._maps_font_scale.setRange(50, 200)
        self._maps_font_scale.setSuffix(" %")
        self._maps_font_scale.setValue(int(self._lc("maps", "map_font_scale", 100)))
        self._maps_font_scale.setToolTip("Scales POI labels, player names, and spawn countdowns.")
        form.addRow("Map label size", self._maps_font_scale)
        self._maps_show_others = QCheckBox(self)
        self._maps_show_others.setChecked(bool(self._lc("maps", "show_other_players", True)))
        self._maps_show_others.setToolTip(
            "Draw other players' shared dots on the map. Off still shares your "
            "own location — it only hides theirs."
        )
        form.addRow("Show other players' dots", self._maps_show_others)

        # Panning was Ctrl+drag and nothing said so, while a plain drag did
        # nothing at all — "I cannot drag the map" is a discoverability report,
        # not a platform bug. Plain drag is the default because it was inert
        # before, so switching it on takes no capability away; Ctrl+drag pans
        # under either value.
        self._maps_pan_mode = QComboBox(self)
        self._maps_pan_mode.addItem("Click and drag", PAN_DRAG)
        self._maps_pan_mode.addItem("Ctrl + click and drag", PAN_CTRL_DRAG)
        self._maps_pan_mode.setCurrentIndex(
            max(0, self._maps_pan_mode.findData(self._lc("maps", "pan_mode", PAN_DRAG)))
        )
        self._maps_pan_mode.setToolTip(
            "Ctrl + drag pans either way. This chooses whether a plain drag "
            "does too. Applies immediately — no restart."
        )
        form.addRow("Pan the map with", self._maps_pan_mode)

        # Transparency: two controls, not one. Window opacity (Settings >
        # Windows) fades the whole window — geometry, labels and player dots
        # with it — so it could never answer "let me see the game through the
        # map without losing the lines". The backdrop fills only behind the
        # map, and the ink always draws at full strength.
        backdrop_box = QGroupBox("Transparency", self)
        backdrop_form = QFormLayout()
        self._maps_backdrop = QSlider(Qt.Orientation.Horizontal, self)
        self._maps_backdrop.setRange(0, 100)
        self._maps_backdrop.setValue(int(self._lc("maps", "backdrop_opacity", 100)))
        backdrop_value = QLabel(f"{self._maps_backdrop.value()}%", self)
        self._maps_backdrop.valueChanged.connect(lambda value: backdrop_value.setText(f"{value}%"))
        backdrop_row = QHBoxLayout()
        backdrop_row.addWidget(self._maps_backdrop, 1)
        backdrop_row.addWidget(backdrop_value, 0)
        backdrop_holder = QWidget(self)
        backdrop_holder.setLayout(backdrop_row)
        self._maps_backdrop.setToolTip(
            "0% is glass — geometry floating on the game. ~60% separates the "
            "lines from what is behind them. 100% reads like a paper map. "
            "Scroll the wheel near a map edge to nudge it without coming here."
        )
        backdrop_form.addRow("Backdrop", backdrop_holder)
        self._maps_fade_idle = QCheckBox(self)
        self._maps_fade_idle.setChecked(bool(self._lc("maps", "backdrop_fade_idle", False)))
        backdrop_form.addRow("Fade when idle", self._maps_fade_idle)
        self._maps_fade_seconds = QSpinBox(self)
        self._maps_fade_seconds.setRange(1, 120)
        self._maps_fade_seconds.setSuffix(" s")
        self._maps_fade_seconds.setValue(int(self._lc("maps", "backdrop_fade_seconds", 5)))
        backdrop_form.addRow("Idle after", self._maps_fade_seconds)
        backdrop_note = chromewidgets.hint(
            "Backdrop only fills behind the map. Lines, labels and player dots "
            "always draw at full strength.",
            self,
        )
        backdrop_form.addRow(backdrop_note)
        backdrop_box.setLayout(backdrop_form)
        form.addRow(backdrop_box)
        self._z_current = QSpinBox(self)
        self._z_closest = QSpinBox(self)
        self._z_other = QSpinBox(self)
        for spin, key, label in (
            (self._z_current, "current_z_alpha", "Current Z opacity"),
            (self._z_closest, "closest_z_alpha", "Closest Z opacity"),
            (self._z_other, "other_z_alpha", "Other Z opacity"),
        ):
            spin.setRange(1, 100)
            spin.setSuffix(" %")
            spin.setValue(int(self._lc("maps", key, 100)))
            form.addRow(label, spin)

        fade_box = QGroupBox("Smooth z-axis fade (when Z layers are off)", self)
        fade_form = QFormLayout()
        self._z_fade_enabled = QCheckBox(self)
        self._z_fade_enabled.setChecked(bool(self._lc("maps", "z_fade_enabled", True)))
        fade_form.addRow("Enabled", self._z_fade_enabled)
        self._z_fade_min = QSpinBox(self)
        self._z_fade_min.setRange(1, 100)
        self._z_fade_min.setSuffix(" %")
        self._z_fade_min.setValue(int(self._lc("maps", "z_fade_min_opacity", 10)))
        self._z_fade_min.setToolTip("Opacity floor for geometry far above/below you.")
        fade_form.addRow("Minimum opacity", self._z_fade_min)
        self._z_fade_strength = QSpinBox(self)
        self._z_fade_strength.setRange(25, 400)
        self._z_fade_strength.setSuffix(" %")
        self._z_fade_strength.setValue(int(self._lc("maps", "z_fade_strength", 100)))
        self._z_fade_strength.setToolTip(
            "Above 100% fades sooner and harder; below 100% keeps distant levels visible longer."
        )
        fade_form.addRow("Fade strength", self._z_fade_strength)
        self._z_fade_fallback = QSpinBox(self)
        self._z_fade_fallback.setRange(0, 1000)
        self._z_fade_fallback.setSpecialValueText("(off)")
        self._z_fade_fallback.setValue(int(self._lc("maps", "z_fade_fallback_height", 0)))
        self._z_fade_fallback.setToolTip(
            "Level height (z-units) assumed for zones without level metadata, "
            "so they fade too. (off) = such zones never fade, like EQTool."
        )
        fade_form.addRow("Fallback level height", self._z_fade_fallback)
        fade_box.setLayout(fade_form)
        form.addRow(fade_box)
        return self._page(form)

    def _apply_maps(self) -> None:
        self._lc_set("maps", "line_width", self._maps_line_width.value())
        self._lc_set("maps", "grid_line_width", self._maps_grid_width.value())
        self._lc_set("maps", "map_font_scale", self._maps_font_scale.value())
        self._lc_set("maps", "show_other_players", self._maps_show_others.isChecked())
        self._lc_set("maps", "pan_mode", self._maps_pan_mode.currentData())
        self._lc_set("maps", "backdrop_opacity", self._maps_backdrop.value())
        self._lc_set("maps", "backdrop_fade_idle", self._maps_fade_idle.isChecked())
        self._lc_set("maps", "backdrop_fade_seconds", self._maps_fade_seconds.value())
        self._lc_set("maps", "current_z_alpha", self._z_current.value())
        self._lc_set("maps", "closest_z_alpha", self._z_closest.value())
        self._lc_set("maps", "other_z_alpha", self._z_other.value())
        self._lc_set("maps", "z_fade_enabled", self._z_fade_enabled.isChecked())
        self._lc_set("maps", "z_fade_min_opacity", self._z_fade_min.value())
        self._lc_set("maps", "z_fade_strength", self._z_fade_strength.value())
        self._lc_set("maps", "z_fade_fallback_height", self._z_fade_fallback.value())

    # -- Windows grid ------------------------------------------------------------------------

    def _build_windows_grid(self) -> QWidget:
        grid = self._windows_grid = QGridLayout()
        grid.addWidget(QLabel("<b>Window</b>"), 0, 0)
        grid.addWidget(QLabel("<b>On top</b>"), 0, 1)
        grid.addWidget(QLabel("<b>Opacity</b>"), 0, 2)
        grid.addWidget(QLabel("<b>Click-through</b>"), 0, 3)
        self._legacy_rows: dict[str, _WindowRow] = {}
        self._new_rows: dict[str, _WindowRow] = {}
        self._plugin_rows: dict[str, _WindowRow] = {}
        row_index = 1
        for label, section in LEGACY_WINDOW_ROWS:
            row = _WindowRow(
                label,
                on_top=bool(self._lc(section, "always_on_top", True)),
                opacity_pct=int(self._lc(section, "opacity", 80)),
                clickthrough=bool(self._lc(section, "clickthrough", False)),
                handle=self._handles.get(section),
            )
            self._legacy_rows[section] = row
            self._add_grid_row(grid, row_index, row)
            row_index += 1
        for label, key in NEW_WINDOW_ROWS:
            state = self._settings.windows.setdefault(key, WindowState())
            row = _WindowRow(
                label,
                on_top=state.always_on_top,
                opacity_pct=round(state.opacity * 100),
                handle=self._handles.get(key),
            )
            self._new_rows[key] = row
            self._add_grid_row(grid, row_index, row)
            row_index += 1

        # Discord extras (bg opacity is the webview's own background).
        self._discord_bg = QSpinBox(self)
        self._discord_bg.setRange(0, 100)
        self._discord_bg.setSuffix(" %")
        self._discord_bg.setValue(int(self._lc("discord", "bg_opacity", 25)))
        grid.addWidget(QLabel("Discord background"), row_index, 0)
        grid.addWidget(self._discord_bg, row_index, 2)
        row_index += 1  # the Discord extras occupy this row too
        grid.setColumnStretch(2, 1)

        # Plugin windows last, and only the ones that actually opened this
        # session — a disabled or errored add-on gets no row, and its saved
        # WindowState is left on disk untouched for when it comes back.
        if self._plugin_windows:
            row_index = self._add_grid_section(grid, row_index, PLUGIN_WINDOWS_SECTION)
            for label, key, widget in self._plugin_windows:
                state = self._settings.windows.setdefault(key, WindowState())
                shown = elide(label)
                row = _WindowRow(
                    shown,
                    on_top=state.always_on_top,
                    opacity_pct=round(state.opacity * 100),
                    handle=widget,
                    # Only worth a tooltip when it says something the row does
                    # not already show.
                    tooltip=label if shown != label else None,
                )
                self._plugin_rows[key] = row
                self._add_grid_row(grid, row_index, row)
                row_index += 1

        outer = QVBoxLayout()
        outer.addLayout(grid)
        note = chromewidgets.hint(
            "Opacity previews immediately; On top / Click-through apply on Save.", self
        )
        outer.addWidget(note)
        outer.addStretch(1)
        body = QWidget(self)
        body.setLayout(outer)
        # Add-ons can push the row count past the window height, and the grid
        # has no other way to reach the bottom rows. (Every page scrolls now;
        # this one needed it first.)
        return self._scrollable(body)

    @staticmethod
    def _add_grid_section(grid: QGridLayout, index: int, title: str) -> int:
        """A full-width rule + subheader; returns the next free row index."""
        rule = QFrame()
        rule.setFrameShape(QFrame.Shape.HLine)
        rule.setFrameShadow(QFrame.Shadow.Sunken)
        grid.addWidget(rule, index, 0, 1, 4)
        header = QLabel(f"<b>{title}</b>")
        grid.addWidget(header, index + 1, 0, 1, 4)
        return index + 2

    @staticmethod
    def _add_grid_row(grid: QGridLayout, index: int, row: _WindowRow) -> None:
        name = QLabel(row.label)
        # Plugin-supplied labels reach this grid, and QLabel auto-detects rich
        # text — an add-on named "Foo & Bar" would render as "Foo  Bar", and
        # one named "<b>x</b>" would style the host's own chrome.
        name.setTextFormat(Qt.TextFormat.PlainText)
        if row.tooltip:
            name.setToolTip(row.tooltip)
        grid.addWidget(name, index, 0)
        grid.addWidget(row.on_top, index, 1)
        grid.addWidget(row.opacity, index, 2)
        if row.clickthrough is not None:
            grid.addWidget(row.clickthrough, index, 3)

    def _apply_windows(self) -> None:
        for section, row in self._legacy_rows.items():
            self._lc_set(section, "always_on_top", row.on_top.isChecked())
            self._lc_set(section, "opacity", row.opacity.value())
            if row.clickthrough is not None:
                self._lc_set(section, "clickthrough", row.clickthrough.isChecked())
            # Apply directly, same as the new rows below — the config_updated
            # signal fires later in apply(), after the save callbacks, and a
            # failure anywhere in between must not leave the legacy windows
            # with stale flags while the new windows already changed.
            handle = self._handles.get(section)
            if handle is not None and hasattr(handle, "apply_window_state"):
                handle.apply_window_state()
        self._lc_set("discord", "bg_opacity", self._discord_bg.value())
        for key, row in self._new_rows.items():
            state = self._settings.windows.setdefault(key, WindowState())
            state.always_on_top = row.on_top.isChecked()
            state.opacity = row.opacity.value() / 100
            handle = self._handles.get(key)
            if handle is not None and hasattr(handle, "apply_window_state"):
                handle.apply_window_state()
        # Plugin rows carry their own handle rather than going through
        # self._handles: that dict is keyed by legacy section name AND window
        # key AND (after app.py wires chat toggles, which happens *after* this
        # window is built) sanitized command name. The row pins its widget at
        # build time so nothing depends on that ordering.
        for key, row in self._plugin_rows.items():
            state = self._settings.windows.setdefault(key, WindowState())
            state.always_on_top = row.on_top.isChecked()
            state.opacity = row.opacity.value() / 100
            handle = row.handle
            if handle is not None and hasattr(handle, "apply_window_state"):
                handle.apply_window_state()

    # -- Audio & Overlays ------------------------------------------------------------------

    def _build_audio_overlays(self) -> QWidget:
        general = self._settings.general
        form = QFormLayout()
        self._voice = QComboBox(self)
        # Store the VoiceInfo.id in userData; index 0 is the empty-id default.
        self._voice.addItem("(system default)", "")
        for voice in list_voices():
            self._voice.addItem(voice.label, voice.id)
        if general.tts_voice:
            index = self._voice.findData(general.tts_voice)
            if index < 0:  # saved voice no longer enumerable — re-add it by id
                self._voice.addItem(general.tts_voice, general.tts_voice)
                index = self._voice.count() - 1
            self._voice.setCurrentIndex(index)
        form.addRow("TTS voice", self._voice)
        self._volume = QSlider(Qt.Orientation.Horizontal, self)
        self._volume.setRange(0, 100)
        self._volume.setValue(general.global_audio_volume)
        form.addRow("Volume", self._volume)
        test_button = QPushButton("Test voice", self)
        test_button.clicked.connect(self._test_voice)
        form.addRow("", test_button)
        self._overlay_seconds = QDoubleSpinBox(self)
        self._overlay_seconds.setRange(1.0, 30.0)
        self._overlay_seconds.setSingleStep(0.5)
        self._overlay_seconds.setValue(general.overlay_text_seconds)
        form.addRow("Alert text duration (s)", self._overlay_seconds)
        # Alert text shadow, size and emphasis live on the Appearance page.
        self._ch_retention = QDoubleSpinBox(self)
        self._ch_retention.setRange(5.0, 300.0)
        self._ch_retention.setSingleStep(5.0)
        self._ch_retention.setValue(general.ch_lane_retention_seconds)
        form.addRow("CH lane retention (s)", self._ch_retention)
        self._ch_tag = QLineEdit(self)
        self._ch_tag.setText(general.ch_chain_tag)
        self._ch_tag.setToolTip(
            "Follow only CH chain calls prefixed with this raid tag "
            "(e.g. 'GG'). Leave blank to follow all calls."
        )
        form.addRow("CH chain tag (blank = all)", self._ch_tag)
        self._ch_cadence = QCheckBox(self)
        self._ch_cadence.setChecked(general.ch_cadence_indicator)
        self._ch_cadence.setToolTip(
            'When the raid leader calls a cadence ("healers to 4 seconds"), show '
            "a muted marker in the CH lane at the declared second. Off by default."
        )
        form.addRow("CH cadence indicator", self._ch_cadence)
        self._ch_cadence_patterns = QPlainTextEdit(self)
        self._ch_cadence_patterns.setPlainText("\n".join(general.ch_cadence_patterns))
        self._ch_cadence_patterns.setFixedHeight(64)
        self._ch_cadence_patterns.setToolTip(
            "Regexes that recognize a cadence callout — one per line, each with a "
            "capturing group ( ) for the seconds. Leave blank to use the defaults."
        )
        form.addRow("CH cadence patterns", self._ch_cadence_patterns)
        self._bard_count = QCheckBox(self)
        self._bard_count.setChecked(general.bard_count_enabled)
        self._bard_count.setToolTip(
            "Show the yellow overlay + speak a tally of bard AoE hits/resists "
            "when a swarm session finalizes (2+ hits only)."
        )
        form.addRow("Bard AoE hit counter", self._bard_count)
        self._root_break_overlay = QCheckBox(self)
        self._root_break_overlay.setChecked(general.root_break_overlay)
        self._root_break_overlay.setToolTip(
            'Show a red "<Spell> has worn off!" overlay alert when one of your '
            "roots breaks (Root, Fetter, Enstill, Immobilize, the Roots line)."
        )
        form.addRow("Root break overlay", self._root_break_overlay)
        self._root_break_audio = QCheckBox(self)
        self._root_break_audio.setChecked(general.root_break_audio)
        self._root_break_audio.setToolTip("Speak the same root-break warning.")
        form.addRow("Speak root break warning", self._root_break_audio)

        mobinfo = self._settings.mobinfo
        mob_box = QGroupBox("Mob Info", self)
        mob_form = QFormLayout()
        self._mobinfo_wiki = QCheckBox(self)
        self._mobinfo_wiki.setChecked(mobinfo.wiki_details)
        self._mobinfo_wiki.setToolTip(
            "Look the considered mob up on wiki.project1999.com and show what "
            "the page says: HP, AC, damage per hit, where it spawns, its "
            "factions and its drop table.\n\n"
            "One request per mob you consider, cached for the session — the "
            "window's own refresh never fetches. Turning this off leaves the "
            "name, the zone and the respawn timer, and contacts the wiki "
            "never."
        )
        mob_form.addRow("Look up wiki details", self._mobinfo_wiki)
        self._mobinfo_image = QCheckBox(self)
        self._mobinfo_image.setChecked(mobinfo.show_image)
        self._mobinfo_image.setToolTip(
            "Show the picture from the mob's wiki page. Downloaded once at "
            "thumbnail size and kept in the local cache; needs the lookup "
            "above."
        )
        mob_form.addRow("Show mob picture", self._mobinfo_image)
        mob_box.setLayout(mob_form)
        form.addRow(mob_box)
        return self._page(form)

    def _test_voice(self) -> None:
        voice = self._voice.currentData() or ""  # id in userData; index 0 -> ""
        speaker = default_speaker(voice=voice, volume=self._volume.value() / 100)
        speaker.speak("nParse plus voice test")

    # -- Sharing --------------------------------------------------------------------------

    def _build_sharing(self) -> QWidget:
        form = QFormLayout()
        self._sharing_mode = QComboBox(self)
        self._sharing_mode.addItems(["pigparse", "nparse", "off"])
        self._sharing_mode.setCurrentText(self._settings.sharing.mode)
        form.addRow("Location sharing", self._sharing_mode)
        # Each direction, said separately, because they are not the same:
        # off stops the connection here and now; on has to build one, and the
        # handlers that would use it took theirs at startup (#69).
        note = chromewidgets.hint(
            "Turning sharing off applies immediately — the connection closes, "
            "nothing further is sent, and remote dots stop. Turning it on, or "
            "switching networks, needs a restart.",
            self,
        )
        form.addRow(note)

        account_box = QGroupBox("pigparse.org account", self)
        account_form = QFormLayout()
        self._account_status = QLabel("", self)
        self._account_status.setWordWrap(True)
        account_form.addRow(self._account_status)
        account_buttons = QHBoxLayout()
        self._account_login = QPushButton("Log in with Discord…", self)
        self._account_login.clicked.connect(self._start_discord_login)
        self._account_logout = QPushButton("Log out", self)
        self._account_logout.clicked.connect(self._discord_logout)
        account_buttons.addWidget(self._account_login)
        account_buttons.addWidget(self._account_logout)
        account_buttons.addStretch(1)
        account_form.addRow(account_buttons)
        account_box.setLayout(account_form)
        form.addRow(account_box)

        # Dump upload is its own box, not part of the pigparse account one:
        # only one of its destinations needs that account at all.
        upload_box = QGroupBox("Character dump upload", self)
        upload_form = QFormLayout()
        self._upload_target = QComboBox(self)
        self._upload_target.addItem("Off", "off")
        self._upload_target.addItem("pigparse.org character page", "pigparse")
        self._upload_target.addItem("p99planner.com", "p99planner")
        index = self._upload_target.findData(self._settings.dumps.upload_target)
        self._upload_target.setCurrentIndex(max(index, 0))
        self._upload_target.currentIndexChanged.connect(lambda _i: self._refresh_upload_note())
        upload_form.addRow("Send character dumps to", self._upload_target)
        self._upload_note = chromewidgets.hint("", self)
        self._upload_note.setWordWrap(True)
        upload_form.addRow(self._upload_note)
        upload_box.setLayout(upload_form)
        form.addRow(upload_box)

        self._refresh_account_status()
        self._refresh_upload_note()
        return self._page(form)

    #: What each dump-upload destination actually does, said plainly — they
    #: differ in what they need from the user, which dumps they take, and
    #: where the data lands.
    UPLOAD_NOTES: ClassVar[dict[str, str]] = {
        "off": "Dumps stay on this machine, in the Character Dumps library.",
        "pigparse": (
            "Uploads your inventory to your pigparse.org character page. "
            "Needs the Discord login above. Spellbook dumps stay local — "
            "pigparse.org has nowhere to put them."
        ),
        "p99planner": (
            "Stages the export at p99planner.com and opens a review page in "
            "your browser, where you approve the import. Takes inventory and "
            "spellbook dumps; a character's pair is reviewed as one entry. "
            "No account or login — the link is the only credential, so treat "
            "it as private. Later dumps join the same link for 24 hours."
        ),
    }

    def _refresh_upload_note(self) -> None:
        target = self._upload_target.currentData() or "off"
        note = self.UPLOAD_NOTES.get(target, "")
        if target != "off":
            note += (
                "\nTakes effect on Apply, and rides on the Character Dumps "
                "window's auto-import — that is what notices the dump."
            )
        self._upload_note.setText(note)

    def _refresh_account_status(self) -> None:
        account = self._settings.pigparse_account
        if account.api_token:
            who = account.username or account.discord_id
            self._account_status.setText(f"Logged in as {who}.")
        else:
            self._account_status.setText(
                "Not logged in. Logging in via Discord enables the auction APIs "
                "and the pigparse.org character browser (inventory upload)."
            )
        self._account_login.setEnabled(True)
        self._account_logout.setEnabled(bool(account.api_token))

    def _start_discord_login(self) -> None:
        """Open the pigparse Discord login in the browser; the user
        authenticates there and the loopback redirect delivers the token."""
        self._account_login.setEnabled(False)
        self._account_status.setText("Waiting for the browser login…")
        login_fn = self._discord_login

        def run() -> None:
            try:
                result = login_fn()
            except Exception:
                result = None
            # Cross-thread emit: Qt queues delivery onto the GUI thread.
            self._discord_auth_done.emit(result)

        threading.Thread(target=run, name="discord-login", daemon=True).start()

    def _finish_discord_login(self, result: object) -> None:
        account = self._settings.pigparse_account
        if isinstance(result, DiscordAuthResult) and result.ok:
            account.username = result.username
            account.discord_id = result.discord_id
            account.api_token = result.api_token
            if self._on_save is not None:
                self._on_save()
        else:
            self._account_status.setText("Login failed or timed out — try again.")
            self._account_login.setEnabled(True)
            return
        self._refresh_account_status()

    def _discord_logout(self) -> None:
        account = self._settings.pigparse_account
        account.username = ""
        account.discord_id = ""
        account.api_token = ""
        if self._on_save is not None:
            self._on_save()
        self._refresh_account_status()

    # -- Advanced (archiving + Night Vision fix) ---------------------------------------------

    def _build_advanced(self) -> QWidget:
        general = self._settings.general
        form = QFormLayout()
        self._archive_enabled = QCheckBox(self)
        self._archive_enabled.setChecked(general.log_archive_enabled)
        form.addRow("Archive oversized logs", self._archive_enabled)
        self._archive_mb = QSpinBox(self)
        self._archive_mb.setRange(1, 4096)
        self._archive_mb.setSuffix(" MB")
        self._archive_mb.setValue(general.log_archive_size_mb)
        form.addRow("Archive threshold", self._archive_mb)

        macros_form = QFormLayout()
        self._socials_autosync = QCheckBox(self)
        self._socials_autosync.setChecked(general.socials_autosync)
        self._socials_autosync.setToolTip(
            "Reads your character ini files and updates nParse+'s own copy. "
            "It never writes into the EQ directory."
        )
        macros_form.addRow("Sync macros when EQ exits", self._socials_autosync)
        self._socials_sync_status = chromewidgets.hint(
            "Captures macros you made in game into the Macro Editor's local copy "
            "when the client closes, so they keep their history and can be restored. "
            "Read-only — nothing is written back to EverQuest.",
            self,
        )
        macros_form.addRow(self._socials_sync_status)
        self._socials_sync_now = QPushButton("Sync now", self)
        self._socials_sync_now.clicked.connect(self._sync_socials_now)
        macros_form.addRow(self._socials_sync_now)
        macros_box = QGroupBox("Macros", self)
        macros_box.setLayout(macros_form)
        form.addRow(macros_box)
        self._refresh_socials_sync_status()

        plugins_form = QFormLayout()
        self._plugins_enabled_box = QCheckBox(self)
        self._plugins_enabled_box.setChecked(self._settings.plugins.enabled)
        plugins_form.addRow("Enable plugins (add-ons)", self._plugins_enabled_box)
        plugins_note = chromewidgets.hint(
            "Add-ons are third-party code that runs with the same access to your "
            "computer as nParse+ itself. Off by default — nParse+ needs none of "
            "them. Turn this on and a Plugins page appears in this window, where "
            "you install add-ons and approve each one before it ever runs.",
            self,
        )
        plugins_form.addRow(plugins_note)
        plugins_box = QGroupBox("Add-ons (plugins)", self)
        plugins_box.setLayout(plugins_form)
        form.addRow(plugins_box)

        visionfix_form = QFormLayout()
        self._visionfix_status = QLabel("", self)
        self._visionfix_status.setWordWrap(True)
        visionfix_form.addRow(self._visionfix_status)
        visionfix_buttons = QHBoxLayout()
        self._visionfix_apply = QPushButton("Apply fix", self)
        self._visionfix_apply.clicked.connect(self._apply_visionfix)
        self._visionfix_revert = QPushButton("Revert", self)
        self._visionfix_revert.clicked.connect(self._revert_visionfix)
        visionfix_buttons.addWidget(self._visionfix_apply)
        visionfix_buttons.addWidget(self._visionfix_revert)
        visionfix_form.addRow(visionfix_buttons)
        visionfix_box = QGroupBox("Night Vision fix", self)
        visionfix_box.setLayout(visionfix_form)
        form.addRow(visionfix_box)
        self._refresh_visionfix_status()
        return self._page(form)

    def _notify_plugins_restart(self, *, enabled: bool) -> None:
        """Tell the user the add-on switch takes effect next launch.

        Plugins cannot be turned on live: activation registers bus
        subscriptions, pipeline parsers and driver ticks, all of which must
        happen before the driver thread starts, and plugin windows must exist
        when the tray menu and window layouts are built. Hot enable/disable is
        tracked separately — see TODO(#45).
        """
        if enabled:
            box = QMessageBox(
                QMessageBox.Icon.Information,
                "Add-ons enabled",
                "Restart nParse+ to load plugins. A Plugins page will then appear "
                "in this window, and any add-on you install has to be approved "
                "before it runs.",
                parent=self,
            )
            open_folder = box.addButton("Open plugins folder", QMessageBox.ButtonRole.ActionRole)
            box.addButton(QMessageBox.StandardButton.Ok)
            box.exec()
            if box.clickedButton() is open_folder:
                from PySide6.QtCore import QUrl
                from PySide6.QtGui import QDesktopServices

                from nparseplus.config.paths import ensure_plugins_dir

                QDesktopServices.openUrl(QUrl.fromLocalFile(str(ensure_plugins_dir())))
            return
        QMessageBox.information(
            self,
            "Add-ons disabled",
            "Plugins will stop loading the next time nParse+ starts. Add-ons "
            "already running this session keep going until you quit.",
        )

    # -- Night Vision fix (moved from PreferencesWindow) --------------------------------------

    def _visionfix_dir(self) -> Path | None:
        text = self._install_dir.path()
        return Path(text).expanduser() if text else None

    def _refresh_visionfix_status(self) -> None:
        if not hasattr(self, "_visionfix_status"):
            return  # General pane builds before Advanced
        eq_dir = self._visionfix_dir()
        reason = visionfix.preflight(eq_dir)
        if reason is not None:
            self._visionfix_status.setText(reason)
            self._visionfix_apply.setEnabled(False)
            self._visionfix_revert.setEnabled(False)
            return
        assert eq_dir is not None
        has_backup = visionfix.backup_exists(eq_dir)
        self._visionfix_status.setText(
            "Applied (backup present — revert available)."
            if has_backup
            else "Replaces night-blind shaders/sky textures. Files are backed up first."
        )
        self._visionfix_apply.setEnabled(True)
        self._visionfix_revert.setEnabled(has_backup)

    def _eq_running(self) -> bool:
        # Shared with the Macro Editor, which needs the same warning before it
        # writes into a character ini.
        return eqprocess.eq_is_running()

    # -- Macro auto-sync ------------------------------------------------------------------

    def _refresh_socials_sync_status(self) -> None:
        if self._socials_sync is None:
            return
        self._socials_sync_status.setText(self._socials_sync.status_text())

    def _sync_socials_now(self) -> None:
        """Fold in-game macro changes into the local copy on demand."""
        if self._socials_sync is None:
            self._socials_sync_status.setText("Macro sync is unavailable.")
            return
        reason = visionfix.preflight(self._visionfix_dir())
        if reason is not None:
            self._socials_sync_status.setText(reason)
            return
        examined = self._socials_sync.sync(datetime.now())
        if not examined:
            self._socials_sync_status.setText(
                "Nothing to sync — no character files have changed since the last check."
            )
            return
        self._refresh_socials_sync_status()

    def _apply_visionfix(self) -> None:
        eq_dir = self._visionfix_dir()
        if self._eq_running():
            answer = QMessageBox.warning(
                self,
                "EverQuest looks like it is running",
                "Apply anyway? The game must be restarted to pick up the fix.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        try:
            written = visionfix.apply_visionfix(eq_dir)
        except Exception as exc:
            QMessageBox.critical(self, "Night Vision fix failed", str(exc))
        else:
            QMessageBox.information(
                self,
                "Night Vision fix applied",
                f"{written} files written (originals backed up to "
                f"{visionfix.BACKUP_DIR_NAME}/). Restart EQ to see the fix.",
            )
        self._refresh_visionfix_status()

    def _revert_visionfix(self) -> None:
        eq_dir = self._visionfix_dir()
        try:
            restored = visionfix.revert_visionfix(eq_dir)
        except Exception as exc:
            QMessageBox.critical(self, "Revert failed", str(exc))
        else:
            QMessageBox.information(
                self, "Night Vision fix reverted", f"{restored} original files restored."
            )
        self._refresh_visionfix_status()

    # -- apply ------------------------------------------------------------------------------------

    def apply(self) -> None:
        general = self._settings.general
        old_log_dir = str(general.eq_log_dir)
        old_install_dir = str(general.eq_install_dir or "")
        old_voice = general.tts_voice
        old_volume = general.global_audio_volume
        old_upload_target = self._settings.dumps.upload_target
        general.eq_log_dir = Path(self._log_dir.path()).expanduser()
        install = self._install_dir.path()
        general.eq_install_dir = Path(install).expanduser() if install else None
        general.update_check = self._update_check.isChecked()
        general.font_size = self._font_size.value()
        general.skin = self.selected_skin()  # type: ignore[assignment]
        general.overlay_text_size = int(self._overlay_text_size.currentData())
        general.alert_emphasis = self._alert_emphasis.currentData()
        general.frame_opacity = self._frame_opacity.value()
        # Persist the VoiceInfo.id from userData (not the label); "" -> None.
        general.tts_voice = self._voice.currentData() or None
        general.global_audio_volume = self._volume.value()
        general.overlay_text_seconds = self._overlay_seconds.value()
        general.overlay_text_shadow = self._overlay_shadow.isChecked()
        general.ch_lane_retention_seconds = self._ch_retention.value()
        general.ch_chain_tag = self._ch_tag.text().strip()
        general.ch_cadence_indicator = self._ch_cadence.isChecked()
        general.ch_cadence_patterns = [
            line.strip()
            for line in self._ch_cadence_patterns.toPlainText().splitlines()
            if line.strip()
        ]
        general.bard_count_enabled = self._bard_count.isChecked()
        general.root_break_overlay = self._root_break_overlay.isChecked()
        general.root_break_audio = self._root_break_audio.isChecked()
        general.log_archive_enabled = self._archive_enabled.isChecked()
        general.log_archive_size_mb = self._archive_mb.value()
        general.socials_autosync = self._socials_autosync.isChecked()
        self._settings.sharing.mode = self._sharing_mode.currentText()  # type: ignore[assignment]
        self._settings.dumps.upload_target = self._upload_target.currentData()
        self._settings.mobinfo.wiki_details = self._mobinfo_wiki.isChecked()
        self._settings.mobinfo.show_image = self._mobinfo_image.isChecked()
        spellwindow = self._settings.spellwindow
        spellwindow.row_sort = self._row_sort_combo.currentData()
        spellwindow.you_only_spells = self._you_only.isChecked()
        spellwindow.show_random_rolls = self._show_rolls.isChecked()
        spellwindow.bar_fade_to_red = self._bar_fade.isChecked()
        spellwindow.show_boats = self._show_boats.isChecked()
        spellwindow.show_mob_timers = self._show_mob_timers.isChecked()
        spellwindow.show_roll_timers = self._show_roll_timers.isChecked()
        spellwindow.show_custom_timers = self._show_custom_timers.isChecked()
        spellwindow.raid_group_by_spell = self._raid_group_by_spell.isChecked()
        spellwindow.best_guess_spells = self._best_guess.isChecked()
        spellwindow.respawn_expiry_audio = self._respawn_audio.isChecked()
        spellwindow.buff_fade_warning_seconds = self._buff_fade_secs.value()
        spellwindow.buff_fade_warning_audio = self._buff_fade_audio.isChecked()
        spellwindow.post_expiry_flash_enabled = self._post_expiry_flash.isChecked()
        spellwindow.post_expiry_flash_seconds = self._post_expiry_secs.value()
        dps = self._settings.dps
        dps.damage_sources = self._dps_sources.currentData()
        dps.spell_credit_window_seconds = self._dps_credit_window.value()
        dps.count_pet_damage = self._dps_count_pet.isChecked()
        dps.fight_retention_seconds = self._dps_retention.value()
        dps.trailing_window_seconds = self._dps_window.value()
        dps.session_min_fight_seconds = self._dps_session_min.value()
        plugins = self._settings.plugins
        plugins_was_enabled = plugins.enabled
        plugins.enabled = self._plugins_enabled_box.isChecked()
        self._apply_character()
        self._apply_maps()
        self._apply_windows()
        for spec, page in self._extra_pages:
            if spec.apply is None:
                continue
            try:
                spec.apply(page)
            except Exception:
                logger.exception("settings page %r failed to apply", spec.title)

        if self._on_save is not None:
            self._on_save()
        if plugins.enabled != plugins_was_enabled:
            self._notify_plugins_restart(enabled=plugins.enabled)
        if self._on_legacy_save is not None:
            self._on_legacy_save()
        if self._notify_legacy is not None:
            self._notify_legacy()  # live-applies legacy window opacity/flags
        if self._repaint_maps is not None:
            self._repaint_maps()  # maps canvas reads its keys at paint time
        if self._on_log_dir_changed is not None and str(general.eq_log_dir) != old_log_dir:
            self._on_log_dir_changed(Path(general.eq_log_dir))
        if (
            self._on_install_dir_changed is not None
            and str(general.eq_install_dir or "") != old_install_dir
        ):
            # Re-resolves spells_us.txt; a no-op unless the file actually
            # moved (the backend decides that, not this window).
            self._on_install_dir_changed()
        if (
            self._on_upload_target_changed is not None
            and self._settings.dumps.upload_target != old_upload_target
        ):
            self._on_upload_target_changed()  # build the destination's plumbing
        if self._on_audio_changed is not None and (
            general.tts_voice != old_voice or general.global_audio_volume != old_volume
        ):
            self._on_audio_changed()  # live-swap the shared TTS speaker
        if self._on_dps_changed is not None:
            self._on_dps_changed()  # push the counting rules onto the tracker
        if self._on_mobinfo_changed is not None:
            # Builds the wiki client/worker if the lookup was just turned on,
            # then re-renders the window for the picture toggle.
            self._on_mobinfo_changed()
        if self._on_overlay_timing_changed is not None:
            self._on_overlay_timing_changed()  # alert duration + CH lane retention
        if self._on_sharing_changed is not None:
            # Turning sharing off applies here; turning it on still needs a
            # restart (see SharingCoordinator.apply_mode).
            self._on_sharing_changed()
        # Skin, frame opacity, alert size/emphasis/shadow all apply live.
        self._skin_on_open = general.skin
        if self._on_appearance_changed is not None:
            self._on_appearance_changed()

    # -- keep normal window mouse behavior (text fields, sliders) ------------------------------

    def _page(self, form: QFormLayout, header: QWidget | None = None) -> QWidget:
        outer = QVBoxLayout()
        if header is not None:
            outer.addWidget(header)
        outer.addLayout(form)
        outer.addStretch(1)
        page = QWidget(self)
        page.setLayout(outer)
        return self._scrollable(page)

    def _scrollable(self, body: QWidget) -> QScrollArea:
        """Put a page in a scroll area so the WINDOW can be small.

        A QStackedWidget's minimum size is the largest of its pages, so
        without this the single widest page set the floor for all of them and
        the window could not be narrowed past it — Sharing did it at ~550px,
        purely because one row is a long label beside a combo listing
        "pigparse.org character page".

        Scrolling is the right answer for a settings page specifically: the
        content is a list of independent controls, so content that runs off
        the edge is still reachable by moving. That is not true of, say, the
        map, which is why this is a habit for this window and not a rule for
        the app.
        """
        page = QScrollArea(self)
        page.setWidgetResizable(True)
        page.setFrameShape(QFrame.Shape.NoFrame)
        page.setWidget(body)
        return page

    def mousePressEvent(self, event) -> None:
        QWidget.mousePressEvent(self, event)

    def mouseMoveEvent(self, event) -> None:
        QWidget.mouseMoveEvent(self, event)

    def mouseReleaseEvent(self, event) -> None:
        QWidget.mouseReleaseEvent(self, event)

    def wheelEvent(self, event) -> None:
        QWidget.wheelEvent(self, event)
