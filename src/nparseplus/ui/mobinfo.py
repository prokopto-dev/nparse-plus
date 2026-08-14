"""Mob info overlay — shows the last-considered mob (MobInfoState).

Port of EQTool's UI/MobInfo.xaml + MobInfoComponents/MobComponent.xaml: the
name, the stat block (HP, AC, damage per hit, attacks per round, attack
speed, run speed, aggro radius), where it spawns and how long it takes to
come back, its factions and specials, the known-loot table with PigParse
6-month WTS average prices, the page's picture, and a link to the wiki page
(#113).

Everything below the name comes from two places and both are third-party
text:

* ``MobInfoState.wiki`` — the P99 wiki page, fetched and parsed in
  ``net.p99wiki`` on the net worker. ``image_path`` is a file the client
  already downloaded, so the only thing this module does with the network is
  read a local PNG — net/ never imports Qt and the GUI thread never blocks.
* ``MobInfoState.loot`` — the merged drop list (see ``consider.merge_loot``).

Because it is third-party text it is HTML-ESCAPED at every point it reaches
a RichText label, and any URL that is not http(s) is dropped rather than
rendered (#102 was this class of bug).

Sizes are multipliers of ``general.font_size``, like every other overlay;
the body scrolls so the picture and a long drop table cannot push the window
past its edges.
"""

from __future__ import annotations

import html
import webbrowser
from collections.abc import Callable, Sequence

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget

from nparseplus.config.settings import Settings
from nparseplus.core.handlers.consider import LootPrice, MobInfoState
from nparseplus.ui import chrome, skins, theme
from nparseplus.ui.overlaybase import OverlayWindowBase, format_mmss
from nparseplus.ui.skinwidgets import SkinPanel, SkinTitleBar

WINDOW_KEY = "mobinfo"
REFRESH_INTERVAL_MS = 500
DEFAULT_GEOMETRY = (640, 420, 260, 260)
WIKI_BASE = "https://wiki.project1999.com"
#: How many drops the window lists before it says "+N more". EQTool caps the
#: list by height instead; a fixed count keeps the fingerprint cheap.
LOOT_LIMIT = 12
#: The picture's ceiling, in multiples of the base font size — a multiplier
#: rather than px so the user's font choice keeps working.
IMAGE_MAX_HEIGHT_EM = 16


def safe_url(url: str | None) -> str:
    """A URL fit to put in an href, or "" — wiki text is not ours."""
    if not url:
        return ""
    return url if url.startswith(("https://", "http://")) else ""


def link_html(text: str, url: str | None, color: str = chrome.LINK) -> str:
    """``text`` as a link when the URL is usable, escaped either way."""
    safe = safe_url(url)
    label = html.escape(text)
    if not safe:
        return label
    return f'<a href="{html.escape(safe, quote=True)}" style="color:{color};">{label}</a>'


def stat_rows(wiki) -> list[tuple[str, str]]:
    """The MobComponent.xaml stat block, minus the rows EQTool comments out.

    Empty fields are dropped rather than rendered blank: a P99 page states
    what it knows, and half of them know nothing about attack speed.
    """
    if wiki is None:
        return []
    candidates = (
        ("HP", wiki.hp),
        ("AC", wiki.ac),
        ("Damage/hit", wiki.damage_per_hit),
        ("Attacks/round", wiki.attacks_per_round),
        ("Attack speed", wiki.attack_speed),
        ("Run speed", wiki.run_speed),
        ("Aggro radius", wiki.aggro_radius),
    )
    return [(label, value) for label, value in candidates if value]


def loot_line(entry: LootPrice, muted: str) -> str:
    """One drop row: name (linked), its price, and the wiki's rarity."""
    row = link_html(entry.name, entry.url)
    if entry.has_price:
        price = f"{html.escape(str(entry.price))}p"
        row += " — " + (link_html(price, entry.price_url) if entry.price_url else price)
    if entry.rarity:
        row += f" <span style='color:{muted};'>({html.escape(entry.rarity)})</span>"
    return row


class MobInfoWindow(OverlayWindowBase):
    def __init__(
        self,
        settings: Settings,
        mob_info: MobInfoState,
        on_save: Callable[[], None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(
            settings=settings,
            window_key=WINDOW_KEY,
            title="Mob Info",
            default_geometry=DEFAULT_GEOMETRY,
            on_save=on_save,
            parent=parent,
        )
        self._mob_info = mob_info
        self.setObjectName("MobInfoWindow")
        self.setMinimumSize(200, 120)
        self._pixmap: QPixmap | None = None
        self._pixmap_path: str = ""

        self._name = QLabel("Consider a mob…", self)
        self._name.setObjectName("MobInfoName")
        self._name.setWordWrap(True)
        self._subtitle = QLabel("", self)
        self._subtitle.setObjectName("MobInfoSubtitle")
        self._subtitle.setWordWrap(True)
        self._subtitle.hide()
        self._detail = QLabel("", self)
        self._detail.setWordWrap(True)
        self._image = QLabel(self)
        self._image.setObjectName("MobInfoImage")
        self._image.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self._image.setMinimumSize(1, 1)
        self._image.hide()
        self._stats = self._rich_label("MobInfoStats")
        self._extra = self._rich_label("MobInfoExtra")
        self._loot = self._rich_label("MobInfoLoot")

        self._wiki_button = QPushButton("Open wiki page", self)
        self._wiki_button.clicked.connect(self._open_wiki)
        self._wiki_button.setEnabled(False)

        self._skin = skins.skin()
        self._title_bar = SkinTitleBar(self._skin, "MOB INFO", parent=self)

        self._container = SkinPanel(self._skin, parent=self)
        self._container.setObjectName("MobInfoContainer")
        layout = QVBoxLayout(self._container)
        layout.setSpacing(4)
        layout.addWidget(self._title_bar)
        body = QVBoxLayout()
        body.setContentsMargins(6, 2, 6, 4)
        body.setSpacing(4)
        body.addWidget(self._name)
        body.addWidget(self._subtitle)
        body.addWidget(self._scrollable_body(), 1)
        body.addWidget(self._wiki_button)
        layout.addLayout(body, 1)

        outer = QVBoxLayout()
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self._container)
        self.setLayout(outer)

        # Poll (cheap) rather than marshalling on_change across threads.
        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self._on_refresh_tick)
        self._refresh_timer.start(REFRESH_INTERVAL_MS)

        self.apply_skin()
        self.restore_visibility()

    def _rich_label(self, name: str) -> QLabel:
        label = QLabel("", self)
        label.setObjectName(name)
        label.setWordWrap(True)
        label.setTextFormat(Qt.TextFormat.RichText)
        label.setOpenExternalLinks(True)
        label.setMinimumWidth(1)
        label.hide()
        return label

    def _scrollable_body(self) -> QScrollArea:
        """Everything past the headline scrolls.

        A picture plus a full drop table is taller than an overlay anybody
        wants over the game, and the window must stay shrinkable to its
        minimum — so the content gets a viewport instead of forcing the
        window's size. The wheel lands here (the window's own wheelEvent is
        inert), which is how scrolling works at all.
        """
        content = QWidget(self)
        content.setObjectName("MobInfoBody")
        inner = QVBoxLayout(content)
        inner.setContentsMargins(0, 0, 0, 0)
        inner.setSpacing(4)
        inner.addWidget(self._detail)
        inner.addWidget(self._image)
        inner.addWidget(self._stats)
        inner.addWidget(self._extra)
        inner.addWidget(self._loot)
        inner.addStretch(1)

        self._scroll = QScrollArea(self)
        self._scroll.setObjectName("MobInfoScroll")
        self._scroll.setWidget(content)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._scroll.setMinimumSize(0, 0)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        return self._scroll

    def apply_skin(self) -> None:
        """Re-style from the active skin — live, no restart (see spellwindow)."""
        self._skin = skins.skin()
        font_size = max(6, self._settings.general.font_size)
        colors = theme.palette()
        self.setStyleSheet(
            skins.overlay_window_style(self._skin, colors, font_size)
            + skins.title_bar_style(self._skin, font_size)
            + skins.scrollbar_style()
            + "#MobInfoName {"
            + skins.typography_style(
                font_size,
                skins.NUMERIC_TEXT,
                color=self._skin.value_color,
            )
            + " background: transparent; }"
            + "#MobInfoSubtitle {"
            + skins.typography_style(font_size, skins.SMALL_DISPLAY, color=self._skin.title_color)
            + " background: transparent; }"
            "#MobInfoScroll, #MobInfoBody { background: transparent; border: 0; }"
            "#MobInfoImage { background: transparent; }"
            f"#MobInfoContainer QPushButton {{ color: {self._skin.title_color};"
            f" background: transparent; border: 1px solid {self._skin.plate_border};"
            " padding: 3px 8px; }"
            f"#MobInfoContainer QPushButton:hover {{"
            f" background: {skins.rgba(self._skin.chrome_accent, 0.14)}; }}"
        )
        self._container.apply_skin(self._skin, self._settings.general.frame_opacity / 100)
        self._title_bar.apply_skin(self._skin)
        # The rendered HTML carries skin/palette colours and the picture is
        # sized off the font, so both have to be rebuilt, not just restyled.
        self._rendered_fingerprint = None
        self.refresh()

    def apply_mobinfo_settings(self) -> None:
        """Settings > Apply: re-render for the picture toggle (#113).

        The lookup itself is the backend's to rewire
        (``Backend.apply_mobinfo_settings``); this is only the display half,
        and it exists because the fingerprint would otherwise see no change.
        """
        self._rendered_fingerprint = None
        self.refresh()

    def _on_refresh_tick(self) -> None:
        """Poll-timer entry: no render work while hidden (showEvent re-renders
        on reopen); refresh() itself stays unguarded for tests/callers."""
        if self.isVisible():
            self.refresh()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.refresh()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)  # base debounce-persists the geometry
        self._rescale_image()

    def refresh(self) -> None:
        # Skip the label/loot-HTML rebuild when nothing about the considered
        # mob changed since the last render (the state is mutated in place on
        # the driver thread; we poll, so compare a value fingerprint).
        mob = self._mob_info
        fingerprint = (
            mob.name,
            mob.zone,
            mob.is_pet,
            mob.spawn_seconds,
            mob.is_notable,
            tuple((e.name, e.price, e.rarity, e.url) for e in mob.loot),
            mob.wiki,  # frozen pydantic model: compares by value
            mob.wiki_unreachable,
            self._settings.mobinfo.show_image,
        )
        if fingerprint == getattr(self, "_rendered_fingerprint", None):
            return
        self._rendered_fingerprint = fingerprint
        if not mob.name:
            self._name.setText("Consider a mob…")
            self._subtitle.hide()
            self._detail.setText("")
            self._clear_image()
            for label in (self._stats, self._extra, self._loot):
                label.setText("")
                label.hide()
            self._wiki_button.setEnabled(False)
            return
        title = mob.name
        if mob.is_notable:
            title += "  ✪"
        if mob.is_pet:
            title += "  (pet)"
        self._name.setText(title)
        # A pet is ours, not a wiki page — never dress it up as one.
        wiki = None if mob.is_pet else mob.wiki
        self._render_subtitle(wiki)
        self._render_detail(mob, wiki)
        self._render_image(wiki)
        self._render_stats(wiki)
        self._render_extra(wiki)
        self._render_loot()
        self._wiki_button.setEnabled(not mob.is_pet)

    # -- rendering ---------------------------------------------------------------

    def _render_subtitle(self, wiki) -> None:
        if wiki is None:
            self._subtitle.setText("")
            self._subtitle.hide()
            return
        parts = [p for p in (wiki.npc_class, wiki.race) if p]
        if wiki.level:
            parts.append(f"Level {wiki.level}")
        self._subtitle.setText(" · ".join(parts))
        self._subtitle.setVisible(bool(parts))

    def _render_detail(self, mob: MobInfoState, wiki) -> None:
        """Zone / spawn location / respawn — plain text, never rich.

        Both respawn figures show when both exist, because they answer
        different questions. ``spawn_seconds`` is the zone database's, which
        is what the spawn timers are built on — but ``ZoneDatabase.spawn_time``
        always answers, falling back to a zone default and then to a global
        6:40, so it cannot say "I don't know about this NPC". The wiki's prose
        ("7 Days (+/- 8 Hours Variance)") often can, and where the two
        disagree that disagreement is the useful part.
        """
        parts = []
        if mob.zone:
            parts.append(f"Zone: {mob.zone}")
        if mob.wiki_unreachable and not mob.is_pet:
            # A failed request is not the same as a page without data. This
            # tells players why no new wiki details appeared (#116).
            parts.append("Wiki: unavailable (could not reach project1999.com)")
        if wiki is not None and wiki.spawn_location:
            parts.append(f"Spawn: {wiki.spawn_location}")
        if mob.spawn_seconds:
            parts.append(f"Respawn: {format_mmss(mob.spawn_seconds)}")
        if wiki is not None and wiki.respawn and wiki.respawn != "?":
            parts.append(f"Wiki respawn: {wiki.respawn}")
        self._detail.setText("\n".join(parts))

    def _render_stats(self, wiki) -> None:
        rows = stat_rows(wiki)
        if not rows:
            self._stats.setText("")
            self._stats.hide()
            return
        muted = theme.palette().hint
        cells = "".join(
            f"<tr><td style='color:{muted};padding-right:10px;'>{html.escape(label)}</td>"
            f"<td>{html.escape(value)}</td></tr>"
            for label, value in rows
        )
        self._stats.setText(f"<table cellspacing='0' cellpadding='0'>{cells}</table>")
        self._stats.show()

    def _render_extra(self, wiki) -> None:
        """Specials, factions, opposing factions, related quests."""
        if wiki is None:
            self._extra.setText("")
            self._extra.hide()
            return
        muted = theme.palette().hint
        sections = (
            ("Specials", wiki.specials),
            ("Factions", wiki.factions),
            ("Opposing", wiki.opposing_factions),
            ("Quests", wiki.related_quests),
        )
        blocks = [
            f"<span style='color:{muted};'>{label}:</span> " + self._link_list(links, muted)
            for label, links in sections
            if links
        ]
        self._extra.setText("<br>".join(blocks))
        self._extra.setVisible(bool(blocks))

    def _link_list(self, links: Sequence[object], muted: str) -> str:
        out = []
        for link in links:
            row = link_html(link.name, link.url)  # type: ignore[attr-defined]
            note = link.note  # type: ignore[attr-defined]
            if note:
                row += f" <span style='color:{muted};'>{html.escape(note)}</span>"
            out.append(row)
        return ", ".join(out)

    def _render_loot(self) -> None:
        loot = self._mob_info.loot
        if not loot:
            self._loot.hide()
            self._loot.setText("")
            return
        muted = theme.palette().hint
        rows = [loot_line(entry, muted) for entry in loot[:LOOT_LIMIT]]
        more = f"<br>… +{len(loot) - LOOT_LIMIT} more" if len(loot) > LOOT_LIMIT else ""
        self._loot.setText(
            f"<span style='color:{muted};'>Known loot:</span><br>" + "<br>".join(rows) + more
        )
        self._loot.show()

    # -- the picture -------------------------------------------------------------

    def _render_image(self, wiki) -> None:
        path = str(wiki.image_path) if (wiki is not None and wiki.image_path) else ""
        if not path or not self._settings.mobinfo.show_image:
            self._clear_image()
            return
        if path != self._pixmap_path:
            pixmap = QPixmap(path)  # a local file the net layer already cached
            if pixmap.isNull():
                self._clear_image()
                return
            self._pixmap, self._pixmap_path = pixmap, path
        self._rescale_image()
        self._image.show()

    def _clear_image(self) -> None:
        self._pixmap, self._pixmap_path = None, ""
        self._image.clear()
        self._image.hide()

    def _rescale_image(self) -> None:
        """Fit the picture to the viewport without ever upscaling it."""
        pixmap = self._pixmap
        if pixmap is None or pixmap.isNull():
            return
        font_size = max(6, self._settings.general.font_size)
        available = max(1, self._scroll.viewport().width() - 4)
        width = min(pixmap.width(), available)
        height = min(pixmap.height(), IMAGE_MAX_HEIGHT_EM * font_size)
        self._image.setPixmap(
            pixmap.scaled(
                width,
                height,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

    def _open_wiki(self) -> None:
        mob = self._mob_info
        if not mob.name:
            return
        # The fetched page's own URL when we have it: it survives the
        # redirects and capitalisation the wiki does to a con'd name.
        url = safe_url(mob.wiki.url) if mob.wiki is not None else ""
        webbrowser.open(url or f"{WIKI_BASE}/{mob.name.strip().replace(' ', '_')}")

    # -- test hooks ------------------------------------------------------------

    def current_name(self) -> str:
        return self._name.text()

    def current_detail(self) -> str:
        return self._detail.text()

    def current_subtitle(self) -> str:
        return self._subtitle.text()

    def current_stats(self) -> str:
        return self._stats.text()

    def current_extra(self) -> str:
        return self._extra.text()

    def current_loot(self) -> str:
        return self._loot.text()

    def has_image(self) -> bool:
        return self._image.isVisible() and self._pixmap is not None

    def wheelEvent(self, event) -> None:  # inert like the other overlays
        event.accept()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.hide()
            self.persist_state(shown=False)
        else:
            super().keyPressEvent(event)
