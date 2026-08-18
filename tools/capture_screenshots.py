#!/usr/bin/env python
"""Generate the documentation screenshots offscreen. One-shot (but rerunnable).

    uv run python tools/capture_screenshots.py            # everything automatable
    uv run python tools/capture_screenshots.py --phase a  # isolated windows only
    uv run python tools/capture_screenshots.py --phase b  # maps (full app)
    uv run python tools/capture_screenshots.py --only window--dps-meter,settings--maps

Each window is constructed under ``QT_QPA_PLATFORM=offscreen``, populated with
synthetic-but-realistic data, and captured with ``QWidget.grab()`` into
``docs/assets/screenshots/<name>.png`` using the exact filenames from
``docs/dev-notes/screenshot-checklist.md``. A MkDocs hook
(``tools/mkdocs_hooks.py``) swaps each "screenshot pending" placeholder for the
real PNG once it exists, so no doc pages need editing.

Two Qt worlds are involved and a process may hold only one ``QApplication``:
phase A wants a bare ``QApplication`` (built from ``composition.build_backend``),
while the maps window and tray menu need the full ``NomnsParse`` app
(``app.create_app``). The parent process runs each phase in its own subprocess.
Phase B backs up and restores the repo-root ``nparse.config.json`` (live,
gitignored dev state) around its run.

Two shots stay manual (their pages keep the "screenshot pending" placeholder):
``home--overview`` (overlays over a live game) and ``window--discord`` (live
Discord voice) need the real game. The tray menu captures automatically now —
built via ``NomnsParse._build_tray_menu`` + ``popup`` so it never enters the
blocking modal ``exec`` that wedges the offscreen platform.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

# Must be set before any PySide6 import: QWidget.grab() renders the widget tree
# into a QPixmap and works headless; QScreen.grabWindow() returns blank offscreen.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# The full app (phase B) builds the legacy Discord QtWebEngine view; offscreen +
# root it needs the Chromium sandbox off or it spams fatal-looking errors.
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--no-sandbox --disable-gpu")
os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = REPO_ROOT / "docs" / "assets" / "screenshots"
SCRATCH = REPO_ROOT / ".screenshots-scratch"

# A frozen naive-local clock so countdowns/DPS render byte-stably (the whole
# pipeline compares naive datetimes — never introduce tz-aware values).
NOW = datetime(2026, 7, 20, 21, 0, 0)

# Compositing backdrops. Panel windows paint a near-opaque dark panel, so the
# backdrop only fills their rounded corners; the two true overlays (event
# overlay, CH lane) are content-only, so the backdrop stands in for "over the
# game".
PANEL_BACKDROP = "#1b1d23"
OVERLAY_BACKDROP = "#0c0e13"


# --------------------------------------------------------------------------- #
# Capture core
# --------------------------------------------------------------------------- #
# Top-level widgets have no QObject parent, so the only strong reference is the
# Python local in each cap function. Once that drops, the widget is collected and
# destroying it offscreen can segfault (often mid-import, during an unrelated GC).
# Retain every window here for the life of the process instead.
_ALIVE: list = []


def _keep(widget):
    """Retain a top-level widget so it is never GC'd mid-run."""
    _ALIVE.append(widget)
    return widget


class _StubSpeaker:
    """A no-op Speaker so build_backend never spawns a TTS subprocess."""

    def speak(self, *_a, **_k) -> None: ...
    def interrupt(self) -> None: ...
    def close(self) -> None: ...


def freeze_now(module, now: datetime = NOW):
    """Swap ``module.datetime`` for a frozen subclass so ``datetime.now()``
    inside that module returns ``now``. Returns a restore callable."""
    real = module.datetime

    class _Frozen(real):  # type: ignore[valid-type, misc]
        @classmethod
        def now(cls, tz=None):
            return now

    module.datetime = _Frozen
    return lambda: setattr(module, "datetime", real)


def capture(
    widget,
    name: str,
    *,
    size=None,
    backdrop: str = PANEL_BACKDROP,
    pad: int = 0,
    show: bool = True,
    hide_after: bool = True,
) -> Path:
    """grab() ``widget`` and composite it over a solid backdrop, then save PNG.

    ``show=False`` grabs the widget without showing it (for an already-visible
    window, or a QMenu whose show() grabs the keyboard and stalls offscreen).
    ``hide_after=False`` leaves a persistent window visible (hiding a full-app
    window offscreen can wedge window activation).
    """
    from PySide6.QtWidgets import QApplication

    if size is not None:
        widget.resize(*size)
    if show:
        widget.show()
    else:
        widget.ensurePolished()
    QApplication.processEvents()
    QApplication.processEvents()
    path = _composite_and_save(widget.grab(), name, backdrop, pad)
    if hide_after:
        widget.hide()
    return path


def _composite_and_save(src, name: str, backdrop: str, pad: int) -> Path:
    """Composite a grabbed pixmap over a solid backdrop and save it as PNG."""
    from PySide6.QtGui import QColor, QPainter, QPixmap

    out = QPixmap(src.width() + 2 * pad, src.height() + 2 * pad)
    out.fill(QColor(backdrop))
    painter = QPainter(out)
    painter.drawPixmap(pad, pad, src)  # SourceOver: composites the grab's alpha
    painter.end()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"{name}.png"
    out.save(str(path))
    print(f"  wrote {name}.png ({out.width()}x{out.height()})")
    return path


# --------------------------------------------------------------------------- #
# Phase A — isolated windows built on a fake backend
# --------------------------------------------------------------------------- #
def _build_backend():
    """A real (Qt-free) backend with no threads/network, seeded with a profile."""
    os.chdir(REPO_ROOT)  # spell gem icons + data/ load relative to CWD
    SCRATCH.mkdir(parents=True, exist_ok=True)
    from nparseplus.composition import build_backend
    from nparseplus.config.settings import PlayerInfo, Settings
    from nparseplus.core.enums import PlayerClass, Server

    settings = Settings()
    settings.general.update_check = False
    settings.general.eq_log_dir = SCRATCH
    settings.sharing.mode = "off"
    settings.players.append(
        PlayerInfo(
            name="Xantik",
            server="green",
            player_class=int(PlayerClass.CLERIC),
            level=60,
            zone="oasis",
            map_location_sharing="everyone",
        )
    )
    backend = build_backend(settings, speaker=_StubSpeaker())
    backend.player.reset_for("Xantik", Server.GREEN)
    return backend, settings


def _spell(backend, name: str):
    """Real Spell (with its gem icon) by name, or a bare fallback."""
    spell = backend.spells.spell_by_name(name)
    if spell is not None:
        return spell
    from nparseplus.core.spells.models import Spell

    return Spell(id=abs(hash(name)) % 99999, name=name)


def seed_spell_timers(backend) -> None:
    """Populate TimersService with the canonical spell-timer sample.

    Split out of ``cap_timers`` so the README product shot (phase B,
    a different process and a different window instance) shows the SAME rows
    as ``window--timers.png``. Two hand-kept copies of this data would
    drift the moment one was edited.
    """
    from nparseplus.core.timers import (
        TRIGGER_TIMER_GROUP,
        YOU_GROUP,
        SpellRow,
        TimerRow,
    )

    t = backend.timers
    t.clear_all()
    # Your buffs (green).
    t.add_spell(
        SpellRow(
            name="Clarity",
            group=YOU_GROUP,
            updated_at=NOW,
            spell=_spell(backend, "Clarity"),
            ends_at=NOW + timedelta(minutes=22),
            total_duration_s=48 * 60.0,
        )
    )
    t.add_spell(
        SpellRow(
            name="Shield of Words",
            group=YOU_GROUP,
            updated_at=NOW,
            spell=_spell(backend, "Shield of Words"),
            ends_at=NOW + timedelta(minutes=6, seconds=40),
            total_duration_s=15 * 60.0,
        )
    )
    # A debuff on the mob (red).
    t.add_spell(
        SpellRow(
            name="Malaise",
            group="a sand giant",
            updated_at=NOW,
            is_target_player=False,
            spell=_spell(backend, "Malaise"),
            ends_at=NOW + timedelta(seconds=72),
            total_duration_s=180.0,
            detrimental=True,
        )
    )
    # Another player's buff (green, target-headed).
    t.add_spell(
        SpellRow(
            name="Aegolism",
            group="Tankenstein",
            updated_at=NOW,
            is_target_player=True,
            spell=_spell(backend, "Aegolism"),
            ends_at=NOW + timedelta(minutes=78),
            total_duration_s=90 * 60.0,
        )
    )
    # A custom trigger timer (purple).
    t.add_timer(
        TimerRow(
            name="Sand Giant pop",
            group=TRIGGER_TIMER_GROUP,
            updated_at=NOW,
            ends_at=NOW + timedelta(minutes=6, seconds=30),
            total_duration_s=420.0,
        )
    )


def cap_timers(backend) -> None:
    from nparseplus.ui.spellwindow import SpellTimerWindow

    seed_spell_timers(backend)
    w = _keep(SpellTimerWindow(backend))
    w._refresh_timer.stop()
    w._flash_timer.stop()
    w.refresh(now=NOW)
    capture(w, "window--timers", size=(250, 430))


def seed_fights(backend) -> None:
    """Populate FightTracker with the canonical DPS sample (see
    ``seed_spell_timers`` for why this is shared rather than duplicated)."""
    from nparseplus.core.events import DamageEvent

    fights = backend.fights
    fights.clear()
    target = "a sand giant"
    # You: sustained damage from NOW-24s..NOW so the entity crosses the 20s
    # session gate and the Best/Current footer populates.
    you_hits = [58, 44, 72, 61, 39, 88, 51, 66, 47, 74, 55, 63]
    for i, dmg in enumerate(you_hits):
        fights.add_damage(
            DamageEvent(
                timestamp=NOW - timedelta(seconds=24 - i * 2),
                target_name=target,
                attacker_name="You",
                damage_done=dmg,
                damage_type="slash",
            )
        )
    for name, hits, step in (
        ("Vebanab", [41, 33, 52, 38, 45, 36], 3),
        ("Roger the Rogue", [63, 71, 58], 6),
    ):
        for i, dmg in enumerate(hits):
            fights.add_damage(
                DamageEvent(
                    timestamp=NOW - timedelta(seconds=22 - i * step),
                    target_name=target,
                    attacker_name=name,
                    damage_done=dmg,
                    damage_type="pierce",
                )
            )
    fights.tick(NOW)  # roll session Best/Current from your entity


def cap_dps_meter(backend) -> None:
    import nparseplus.ui.dpswindow as dpswindow
    from nparseplus.ui.dpswindow import DpsMeterWindow

    seed_fights(backend)
    restore = freeze_now(dpswindow)
    try:
        w = _keep(DpsMeterWindow(backend))
        w._refresh_timer.stop()
        w.refresh()
        capture(w, "window--dps-meter", size=(300, 360))
    finally:
        restore()


def cap_mob_info(backend, settings) -> None:
    from nparseplus.core.handlers.consider import LootPrice
    from nparseplus.ui.mobinfo import MobInfoWindow

    wiki = "https://wiki.project1999.com"
    state = backend.mob_info
    state.name = "Gorenaire"
    state.zone = "The Dreadlands"
    state.spawn_seconds = 25200
    state.is_notable = True
    state.is_pet = False
    state.loot = [
        LootPrice(name=n, url=f"{wiki}/{n.replace(' ', '_')}", price=p)
        for n, p in (
            ("Blue Diamond", "850"),
            ("Circlet of Shadow", "2200"),
            ("Words of the Suffering", "600"),
            ("Diamond", "300"),
            ("Sarnak Battle Shield", "450"),
        )
    ]
    w = _keep(MobInfoWindow(settings, state))
    w._refresh_timer.stop()
    w.refresh()
    capture(w, "window--mob-info", size=(300, 260))


def cap_console(backend, settings) -> None:
    from nparseplus.core.events import LineEvent
    from nparseplus.ui.consolewindow import ConsoleWindow

    lines = [
        "You have entered The Dreadlands.",
        "You begin casting Complete Heal.",
        "You have healed Tankenstein for 1000 points of damage.",
        "A sand giant hits YOU for 84 points of damage.",
        "You slash a sand giant for 72 points of damage.",
        "Vebanab pierces a sand giant for 41 points of damage.",
        "Gorenaire has become ENRAGED.",
        "You gain experience!!",
        "Tankenstein tells the group, 'CH rotation, healers to 4'",
        "A sand giant has been slain by Roger the Rogue!",
        "**A Magic Die is rolled by Whitewitch.",
        "**It could have been any number from 0 to 333, but this time it turned up a 287.",
        "Your Location is 512.40, -1043.10, -84.30",
        "You say, 'Hail, a Karnor guard'",
    ]
    w = _keep(ConsoleWindow(settings))
    for i, line in enumerate(lines):
        ts = NOW - timedelta(seconds=(len(lines) - i) * 3)
        w.handle_event(LineEvent(timestamp=ts, line=line, line_number=i + 1))
    capture(w, "window--console", size=(620, 360))


def seed_alert(w) -> None:
    """Fire the canonical alert + two draining timer bars into an overlay.

    Shared with the README product shot. The alert text is deliberately the
    split form ("kicker — HEADLINE"): #107 was a regression where the headline
    rendered zero pixels wide while the kicker still drew, so an unsplit string
    showed literally nothing. This sample exercises both halves, which makes
    the screenshot a check on that path and not just decoration.
    """
    from nparseplus.core.events import OverlayEvent, TimerBarEvent

    w.handle_event(OverlayEvent(text="Gorenaire — ENRAGED", foreground="Yellow"))
    w.handle_event(TimerBarEvent(name="Stun Breath", total_seconds=30, bar_color="Gold"))
    w.handle_event(TimerBarEvent(name="Dragon Roar", total_seconds=12, bar_color="Red"))
    w._clear_timer.stop()
    w._bar_timer.stop()
    w._sweep_timer.stop()
    # Show the bars part-drained rather than freshly full.
    for name, remaining in (("Stun Breath", 18), ("Dragon Roar", 7)):
        entry = w._bars.get(name)
        if entry is not None:
            entry.ends_at = NOW + timedelta(seconds=remaining)
            w._render_bar(entry, NOW)


def cap_event_overlay() -> None:
    import nparseplus.ui.eventoverlay as eventoverlay
    from nparseplus.config.settings import WindowState
    from nparseplus.ui.eventoverlay import EventOverlayWindow

    restore = freeze_now(eventoverlay)
    try:
        w = _keep(EventOverlayWindow(state=WindowState(geometry=(0, 0, 820, 460))))
        seed_alert(w)
        capture(w, "window--event-overlay", size=(820, 460), backdrop=OVERLAY_BACKDROP)
    finally:
        restore()


def cap_ch_chain() -> None:
    from PySide6.QtWidgets import QApplication

    import nparseplus.ui.eventoverlay as eventoverlay
    from nparseplus.config.settings import WindowState
    from nparseplus.core.events import CompleteHealCadenceEvent, CompleteHealEvent
    from nparseplus.ui.eventoverlay import EventOverlayWindow

    restore = freeze_now(eventoverlay)
    try:
        w = _keep(
            EventOverlayWindow(state=WindowState(geometry=(0, 0, 760, 260)), ch_lane_retention_s=60)
        )
        w.handle_event(CompleteHealCadenceEvent(timestamp=NOW, seconds=4))
        lanes = [("Tankenstein", [2, 5]), ("Offtank Bob", [3]), ("Chanter Su", [6, 8])]
        for target, _cells in lanes:
            w.handle_event(
                CompleteHealEvent(
                    timestamp=NOW, recipient=target, tag="", position="001", caster="Healbot"
                )
            )
        w._sweep_timer.stop()
        w.show()
        QApplication.processEvents()
        # Replace the freshly-launched (right-edge) animated chips with static
        # chips at chosen cells so the lane reads as chips mid-flight.
        for target, cells in lanes:
            lane = w._chain_lanes.get(target)
            if lane is None:
                continue
            for chip in list(lane.chips):
                chip.deleteLater()
            lane.chips.clear()
            for pos, cell in enumerate(cells, start=1):
                lane.add_static_chip(f"{pos:03d}", cell)
        QApplication.processEvents()
        capture(w._lanes_host, "feature--ch-chain", backdrop=OVERLAY_BACKDROP, pad=14)
    finally:
        restore()


def cap_trigger_editor(backend, settings) -> None:
    from nparseplus.ui.triggereditor import TriggerEditorWindow

    w = _keep(TriggerEditorWindow(settings, backend.trigger_engine, on_save=lambda: None))
    w.confirm_unsaved = False
    w.create_group("Raid Alerts")
    tid = next(
        (t.trigger_id for t in w._working if (t.trigger_name or "").startswith("Death Touch")),
        None,
    )
    if tid is not None:
        w.select_trigger(tid)
    w.test_line_edit.setText("Fright says, 'Ykesha'")
    w.run_test()
    capture(w, "window--trigger-editor", size=(960, 640))


def cap_macro_editor(backend, settings) -> None:
    import tempfile

    from nparseplus.ui.macroeditor import MacroEditorWindow

    # A throwaway EQ install so the editor has real files to read. Writing
    # temp files is fine here — this is a tool, not library code.
    eq_dir = Path(tempfile.mkdtemp(prefix="nparseplus-shots-eq-"))
    (eq_dir / "uifiles").mkdir()
    (eq_dir / "eqgame.exe").write_text("")
    macros = [
        (1, 1, "Assist", 13, ["/assist", "/pet attack"]),
        (1, 2, "Pull", 4, ["/shout Pulling %T"]),
        (1, 4, "CH Chain", 7, ["/gu CH on %T"]),
        (1, 5, "Rez", 10, ["/say Need rez please"]),
        (1, 7, "Med", 3, ["/sit"]),
        (2, 1, "Track", 9, ["/tracking"]),
    ]
    lines = ["[Defaults]", "Version=1", "", "[Socials]"]
    for page, button, name, color, cmds in macros:
        prefix = f"Page{page}Button{button}"
        lines.append(f"{prefix}Name={name}")
        lines.append(f"{prefix}Color={color}")
        lines.extend(f"{prefix}Line{i}={cmd}" for i, cmd in enumerate(cmds, start=1))
    lines += ["", "[Friends]", "Friend0=Alistra", "", "[KeyMaps]", "Forward=W"]
    (eq_dir / "Xantik_P1999Green.ini").write_text("\n".join(lines) + "\n")
    (eq_dir / "Beeta_P1999Green.ini").write_text("[Socials]\nPage1Button1Name=Old\n")

    previous = settings.general.eq_install_dir
    settings.general.eq_install_dir = eq_dir
    try:
        store_dir = Path(tempfile.mkdtemp(prefix="nparseplus-shots-store-"))
        w = _keep(MacroEditorWindow(settings, on_save=lambda: None, store_dir=store_dir))
        w.confirm_unsaved = False
        w.warn_eq_running = False
        index = w.character_combo.findText("Xantik")
        if index >= 0:
            w.character_combo.setCurrentIndex(index)
        w.load()
        w.select_slot(1, 1)
        capture(w, "window--macro-editor", size=(960, 640))
    finally:
        settings.general.eq_install_dir = previous


def cap_character_dumps(backend, settings) -> None:
    """The dump library with two characters, both kinds, and some history.

    Built by storing snapshots directly rather than by running the watcher:
    the shot wants a settled library, and the watcher's job (noticing files)
    is not what the window shows.
    """
    import tempfile

    from nparseplus.core.dumps import DumpKind, DumpLibrary, build_dump
    from nparseplus.ui.dumpswindow import CharacterDumpsWindow

    inventory = "\n".join(
        ["\t".join(("Location", "Name", "ID", "Count", "Slots"))]
        + [
            "\t".join((location, name, str(item_id), str(count), str(slots)))
            for location, name, item_id, count, slots in (
                ("Charm", "Empty", 0, 0, 0),
                ("Head", "Crown of Rile", 5310, 1, 5),
                ("Neck", "Golden Amber Necklace", 6041, 1, 5),
                ("Shoulders", "Obulus Death Shroud", 4315, 1, 5),
                ("Back", "Cloak of Flames", 6360, 1, 5),
                ("Primary", "Fist of Zek", 20527, 1, 5),
                ("Secondary", "Shield of Rainbow Hues", 20528, 1, 5),
                ("Fingers", "Silver Disc", 7561, 1, 5),
                ("General1", "Large Bag", 17969, 1, 8),
                ("General1Slot1", "Blue Diamond", 20507, 4, 0),
                ("General1Slot2", "Words of the Suffering", 16281, 1, 0),
                ("General2", "Bag of the Tinkerers", 17300, 1, 10),
                ("Bank1", "Peridot", 10021, 12, 0),
            )
        ]
    )
    spellbook = "\n".join(
        f"{level}\t{name}"
        for level, name in (
            (51, "Superior Healing"),
            (49, "Shield of Thorns"),
            (49, "Engulfing Roots"),
            (44, "Chloroplast"),
            (39, "Skin like Diamond"),
            (29, "Ensnare"),
            (19, "Superior Camouflage"),
            (14, "Spirit of Wolf"),
        )
    )
    # The newest spellbook adds one spell, so the window's change line reads
    # as a real "+1: …" rather than "oldest snapshot".
    spellbook_new = spellbook + "\n51\tCircle of Winter"

    root = Path(tempfile.mkdtemp(prefix="nparseplus-shots-dumps-"))
    library = DumpLibrary(root)
    for character, kind, text, when in (
        ("Prokopton", DumpKind.INVENTORY, inventory, NOW - timedelta(days=6, hours=3)),
        ("Prokopton", DumpKind.SPELLBOOK, spellbook, NOW - timedelta(days=9)),
        ("Prokopton", DumpKind.SPELLBOOK, spellbook_new, NOW - timedelta(hours=5)),
        ("Untune", DumpKind.INVENTORY, inventory, NOW - timedelta(days=1, hours=2)),
    ):
        dump = build_dump(text, character=character, kind=kind, captured_at=when)
        if dump is not None:
            library.store(dump, now=NOW)

    w = _keep(CharacterDumpsWindow(settings, library, on_save=lambda: None))
    w._timer.stop()  # no live re-reads while we compose the shot
    w.refresh()
    ref = library.latest("Prokopton", DumpKind.SPELLBOOK)
    if ref is not None:
        w.select_snapshot(ref)
    capture(w, "window--character-dumps", size=(900, 560))


def cap_trigger_activity(backend, settings) -> None:
    """The Activity tab (#31) with a few representative fires."""
    from datetime import datetime

    from PySide6.QtWidgets import QApplication

    from nparseplus.core.events import TriggerFiredEvent
    from nparseplus.ui.triggereditor import TriggerEditorWindow

    w = _keep(TriggerEditorWindow(settings, backend.trigger_engine, on_save=lambda: None))
    w.confirm_unsaved = False
    base = datetime(2026, 7, 14, 21, 4, 6)
    fires = [
        dict(
            trigger_name="Death Touch",
            group="Encounters",
            display_text="DEATH TOUCH on Gandalf",
            tts_text="death touch",
            line="Fright says, 'Ykesha'",
        ),
        dict(
            trigger_name="Rampage warning",
            group="Raid Pack / Sebilis",
            display_text="a mycolonial guard RAMPAGE",
            timer_name="a mycolonial guard rampage",
            timer_seconds=30,
            line="a mycolonial guard goes on a rampage!",
        ),
        dict(
            trigger_name="Rampage warning",
            group="Raid Pack / Sebilis",
            phase="timer_ending",
            display_text="Rampage in 5",
            timer_name="a mycolonial guard rampage",
            timer_seconds=30,
            line="a mycolonial guard goes on a rampage!",
        ),
        dict(
            trigger_name="Assist call",
            group="Raid Pack / Velious",
            tts_text="assist on Tunare",
            line="Raidleader tells the raid,  'assist Tunare'",
        ),
    ]
    for offset, fire in enumerate(fires):
        w.activity.handle_event(
            TriggerFiredEvent(
                timestamp=base.replace(second=base.second + offset * 3),
                trigger_id=f"demo-{offset}",
                **fire,
            )
        )
    w.tabs.setCurrentIndex(1)
    QApplication.processEvents()
    capture(w, "window--trigger-activity", size=(960, 480))


# (sidebar title, output name). Selected by TITLE, never by row index.
#
# This used to be a list of hardcoded indices tracking the sidebar order in
# UnifiedSettingsWindow.__init__. When a "DPS Meter" page landed at row 5
# (post-2.2), every later index shifted by one and the list went silently
# wrong: settings--maps.png would have captured the DPS page, settings--
# advanced.png would have captured Sharing, and Advanced would never have
# been captured at all — each one saved under a filename that lies about it.
# A missing screenshot renders an honest placeholder; a mislabelled one does
# not. Titles are stable across insertions, so they are what we match.
SETTINGS_PAGES = [
    ("General", "settings--overview"),
    ("General", "settings--general"),
    ("Appearance", "settings--appearance"),
    ("Character", "settings--character"),
    ("Friends", "settings--friends"),
    ("Timers", "settings--timers"),
    ("DPS Meter", "settings--dps-meter"),
    ("Maps", "settings--maps"),
    ("Windows", "settings--windows"),
    ("Audio & Overlays", "settings--audio-overlays"),
    ("Sharing", "settings--sharing"),
    ("Advanced", "settings--advanced"),
]


def _select_settings_page(window, title: str) -> None:
    """Select the settings sidebar row named ``title``.

    Raises rather than falling back to "whatever is showing": capturing the
    wrong page under the right filename is exactly the failure this replaced,
    and it is invisible in the output. A renamed page should stop the run.
    """
    from PySide6.QtCore import Qt

    items = window._sidebar.findItems(title, Qt.MatchFlag.MatchExactly)
    if not items:
        have = [window._sidebar.item(i).text() for i in range(window._sidebar.count())]
        raise SystemExit(f"settings page {title!r} not found — sidebar has {have}")
    window._sidebar.setCurrentRow(window._sidebar.row(items[0]))


def cap_settings(backend, settings) -> None:
    import nparseplus.ui.settingswindow as settingswindow
    from nparseplus.audio.tts import VoiceInfo
    from nparseplus.ui.settingswindow import UnifiedSettingsWindow

    # Deterministic voice list (headless list_voices() is empty/slow).
    settingswindow.list_voices = lambda: [
        VoiceInfo(id="say:Samantha", label="Samantha", engine="say"),
        VoiceInfo(id="say:Alex", label="Alex", engine="say"),
        VoiceInfo(id="say:Daniel", label="Daniel (Natural)", engine="say"),
    ]
    legacy = {
        "maps": {"line_width": 1, "grid_line_width": 1, "current_z_alpha": 100},
        "discord": {},
    }
    w = _keep(
        UnifiedSettingsWindow(
            settings,
            on_save=lambda: None,
            legacy_config=legacy,
            backend_player=backend.player,
            zones=backend.zones,
        )
    )
    w._friends_text.setPlainText("Alistra\nBorin Stoutmug\nCaldwell\nDelphine\nEbonhawk")
    # Display-only: the real value points at this checkout's scratch dir, so the
    # published shot used to carry the generating machine's worktree path. Show
    # what a user's own setting looks like instead. Purely cosmetic — nothing is
    # applied from this window during a capture.
    #
    # The install dir is deliberately left EMPTY. Filling it with a matching
    # fake sends Advanced's Night Vision panel into "Not a directory: …", and a
    # docs screenshot showing an error state is worse than one showing the
    # neutral "Set the EQ install directory first." a new user actually sees.
    w._log_dir.edit.setText("/Users/you/Library/Application Support/EverQuest/Logs")
    for title, name in SETTINGS_PAGES:
        _select_settings_page(w, title)
        capture(w, name, size=(700, 800))


def _spell_window(backend):
    """A SpellTimerWindow with its live timers stopped, ready to grab."""
    from nparseplus.ui.spellwindow import SpellTimerWindow

    w = _keep(SpellTimerWindow(backend))
    w._refresh_timer.stop()
    w._flash_timer.stop()
    return w


def cap_timers_raid(backend) -> None:
    from nparseplus.core.timers import YOU_GROUP, SpellRow

    t = backend.timers
    t.clear_all()
    t.add_spell(
        SpellRow(
            name="Clarity",
            group=YOU_GROUP,
            updated_at=NOW,
            spell=_spell(backend, "Clarity"),
            ends_at=NOW + timedelta(minutes=22),
            total_duration_s=48 * 60.0,
        )
    )
    # The same raid buff on many targets: targets outnumber spells, so raid mode
    # flips this group to one spell header with a row per target.
    for target in ("Tankenstein", "Offtank Bob", "Chanter Su", "Healbot", "Rangerella"):
        t.add_spell(
            SpellRow(
                name="Aegolism",
                group=target,
                updated_at=NOW,
                is_target_player=True,
                spell=_spell(backend, "Aegolism"),
                ends_at=NOW + timedelta(minutes=78),
                total_duration_s=90 * 60.0,
            )
        )
    sw = backend.settings.spellwindow
    previous = sw.raid_group_by_spell
    sw.raid_group_by_spell = True
    try:
        w = _spell_window(backend)
        w.refresh(now=NOW)
        capture(w, "window--timers-raid", size=(250, 300))
    finally:
        sw.raid_group_by_spell = previous


def cap_respawn_timers(backend) -> None:
    from nparseplus.core.timers import MOB_TIMER_GROUP, TimerRow

    t = backend.timers
    t.clear_all()
    # "--Dead-- <victim>" respawn countdowns (a duplicate shows the numbering).
    for name, secs in (
        ("--Dead-- a sand giant", 6 * 60 + 30),
        ("--Dead-- a sand giant", 6 * 60 + 30),
        ("--Dead-- Vessel Drozlin", 28 * 60),
    ):
        t.add_timer(
            TimerRow(
                name=name,
                group=MOB_TIMER_GROUP,
                updated_at=NOW,
                ends_at=NOW + timedelta(seconds=secs),
                total_duration_s=float(secs),
            ),
            allow_duplicates=True,
        )
    w = _spell_window(backend)
    w.refresh(now=NOW)
    capture(w, "feature--respawn-timers", size=(260, 190))


def cap_boats(backend) -> None:
    from nparseplus.core.handlers.boat import BOATS_GROUP
    from nparseplus.core.timers import TimerRow

    t = backend.timers
    t.clear_all()
    for name, mins in (
        ("Butcherblock → Freeport", 7),
        ("Freeport → Butcherblock", 3),
        ("Timorous → Overthere", 11),
    ):
        t.add_timer(
            TimerRow(
                name=name,
                group=BOATS_GROUP,
                updated_at=NOW,
                ends_at=NOW + timedelta(minutes=mins),
                total_duration_s=mins * 60.0,
            ),
            allow_duplicates=True,
        )
    w = _spell_window(backend)
    w.refresh(now=NOW)
    capture(w, "feature--boats", size=(290, 175))


def cap_roll_rows(backend) -> None:
    from nparseplus.core.timers import CounterRow, RollRow

    t = backend.timers
    t.clear_all()
    roll_group = " Random -- 333"
    for roller, value in (("Whitewitch", 287), ("Grimtusk", 201), ("Sylvara", 118)):
        t.add_roll(
            RollRow(
                name=roller,
                group=roll_group,
                updated_at=NOW,
                roll=value,
                max_roll=333,
                ends_at=NOW + timedelta(seconds=95),
                total_duration_s=120.0,
            )
        )
    # A resist tally (xN) on the mob.
    for _ in range(4):
        t.add_counter(
            CounterRow(name="Tashan", group="a sand giant", updated_at=NOW, is_target_player=False)
        )
    w = _spell_window(backend)
    w.refresh(now=NOW)
    capture(w, "feature--roll-rows", size=(270, 230))


def cap_rebuff_flash(backend) -> None:
    from nparseplus.core.timers import YOU_GROUP, SpellRow

    t = backend.timers
    t.clear_all()
    t.add_spell(
        SpellRow(
            name="Clarity",
            group=YOU_GROUP,
            updated_at=NOW,
            spell=_spell(backend, "Clarity"),
            ends_at=NOW + timedelta(minutes=22),
            total_duration_s=48 * 60.0,
        )
    )
    # An expired self-buff lingering as a flashing REBUFF prompt (#16).
    t.add_spell(
        SpellRow(
            name="Aegolism",
            group=YOU_GROUP,
            updated_at=NOW,
            spell=_spell(backend, "Aegolism"),
            ends_at=NOW - timedelta(seconds=5),
            total_duration_s=90 * 60.0,
            post_expiry_persist_s=30.0,
            expired_at=NOW - timedelta(seconds=5),
        )
    )
    w = _spell_window(backend)
    w._flash_on = True  # freeze in the lit flash phase so REBUFF reads clearly
    w.refresh(now=NOW)
    capture(w, "feature--rebuff-flash", size=(250, 170))


def cap_update_dialog() -> None:
    from nparseplus.ui.updatewindow import UpdateAvailableDialog
    from nparseplus.updater import ReleaseInfo, ReleaseNote

    # Real, recent release notes rather than invented ones. A mock offering
    # 1.12.0 over 1.10.0 kept shipping in the docs long after the app reached
    # 2.9.x, which reads to a user as a screenshot of some other program.
    # Keep these roughly current when regenerating.
    release = ReleaseInfo(
        version="2.9.2",
        html_url="https://github.com/prokopto-dev/nparse-plus/releases/tag/v2.9.2",
        notes=(
            ReleaseNote(
                version="2.9.2",
                body="- fix(overlay): stop laying the alert viewport out zero pixels "
                "wide (#108)\n"
                "- Alerts fired audibly but rendered nothing; the headline is back.",
            ),
            ReleaseNote(
                version="2.9.1",
                body="- fix(packaging): make icon.svg readable by appstreamcli so the "
                "Linux release builds (#106)",
            ),
        ),
    )
    w = _keep(UpdateAvailableDialog(release, installed_version="2.9.0"))
    capture(w, "window--update-available", size=(700, 520))


def cap_overlay_utility() -> None:
    import nparseplus.ui.eventoverlay as eventoverlay
    from nparseplus.config.settings import WindowState
    from nparseplus.core.events import CompleteHealCadenceEvent, OverlayEvent, TimerBarEvent
    from nparseplus.ui.eventoverlay import EventOverlayWindow

    restore = freeze_now(eventoverlay)
    try:
        w = _keep(EventOverlayWindow(state=WindowState(geometry=(0, 0, 760, 300))))
        w.handle_event(CompleteHealCadenceEvent(timestamp=NOW, seconds=4))
        w.handle_event(
            OverlayEvent(
                text="Rebuff: Tankenstein — Aegolism faded",
                foreground="Gold",
                section="utility",
            )
        )
        w.handle_event(OverlayEvent(text="Out of Mana", foreground="Red", section="utility"))
        w.handle_event(TimerBarEvent(name="Rebuff window", total_seconds=30, bar_color="Gold"))
        w._clear_timer.stop()
        w._bar_timer.stop()
        w._sweep_timer.stop()
        for tmr in w._utility_timers.values():
            tmr.stop()
        entry = w._bars.get("Rebuff window")
        if entry is not None:
            entry.ends_at = NOW + timedelta(seconds=20)
            w._render_bar(entry, NOW)
        capture(w, "feature--overlay-utility", size=(760, 300), backdrop=OVERLAY_BACKDROP)
    finally:
        restore()


PHASE_A = {
    "window--timers": lambda b, s: cap_timers(b),
    "window--timers-raid": lambda b, s: cap_timers_raid(b),
    "feature--respawn-timers": lambda b, s: cap_respawn_timers(b),
    "feature--boats": lambda b, s: cap_boats(b),
    "feature--roll-rows": lambda b, s: cap_roll_rows(b),
    "feature--rebuff-flash": lambda b, s: cap_rebuff_flash(b),
    "window--dps-meter": lambda b, s: cap_dps_meter(b),
    "window--mob-info": lambda b, s: cap_mob_info(b, s),
    "window--console": lambda b, s: cap_console(b, s),
    "window--event-overlay": lambda b, s: cap_event_overlay(),
    "feature--overlay-utility": lambda b, s: cap_overlay_utility(),
    "feature--ch-chain": lambda b, s: cap_ch_chain(),
    "window--update-available": lambda b, s: cap_update_dialog(),
    "window--trigger-editor": lambda b, s: cap_trigger_editor(b, s),
    "window--macro-editor": lambda b, s: cap_macro_editor(b, s),
    "window--character-dumps": lambda b, s: cap_character_dumps(b, s),
    "window--trigger-activity": lambda b, s: cap_trigger_activity(b, s),
    "settings": lambda b, s: cap_settings(b, s),  # emits all settings--*.png
}


def _apply_app_chrome(app) -> None:
    """Dress the standard-widget windows exactly as ``app.create_app`` does.

    This used to hand-roll a dark Fusion palette and read data/ui/_.css, which
    only approximated the real thing — the docs could drift from the app
    without anyone noticing. Both are generated now, so call the same function
    the app calls and the screenshots cannot disagree with what ships.
    """
    from PySide6.QtGui import QFontDatabase

    for font in ("NotoSans-Regular.ttf", "NotoSans-Bold.ttf"):
        path = REPO_ROOT / "data" / "fonts" / font
        if path.is_file():
            QFontDatabase.addApplicationFont(str(path))

    from nparseplus.ui import chromewidgets

    app.setStyle("Fusion")
    chromewidgets.apply_app_chrome(app, 12)


def run_phase_a(only: set[str] | None) -> None:
    from PySide6.QtWidgets import QApplication

    app = QApplication(sys.argv[:1])
    backend, settings = _build_backend()
    _apply_app_chrome(app)
    for key, fn in PHASE_A.items():
        if only is not None and key not in only and key != "settings":
            continue
        if (
            only is not None
            and key == "settings"
            and not any(o.startswith("settings--") for o in only)
        ):
            continue
        fn(backend, settings)
    sys.stdout.flush()
    os._exit(0)  # skip Qt/interpreter teardown (offscreen destruction can segfault)


# --------------------------------------------------------------------------- #
# Phase B — maps + tray, built on the full NomnsParse app (create_app)
# --------------------------------------------------------------------------- #
def _write_scratch_settings(path: Path) -> None:
    from nparseplus.config.settings import PlayerInfo, Settings, save_settings
    from nparseplus.core.enums import PlayerClass

    s = Settings()
    s.general.update_check = False
    s.general.eq_log_dir = SCRATCH
    s.sharing.mode = "off"
    s.players.append(
        PlayerInfo(name="Xantik", server="green", player_class=int(PlayerClass.CLERIC), level=60)
    )
    save_settings(s, path)


def _inject_map(maps, extra_dots: bool, size: tuple[int, int] = (900, 680)):
    """Place your marker (with a heading arrow), other players' dots, and a
    spawn-point countdown near the loaded zone's center."""
    from nparseplus.parsers.maps.mapclasses import MapPoint

    canvas = maps._map
    geo = canvas._data.geometry
    cx, cy = geo.center_x, geo.center_y
    w = float(geo.width or 1000)
    h = float(geo.height or 1000)

    def point(fx: float, fy: float) -> MapPoint:
        return MapPoint(x=cx + w * fx, y=cy + h * fy, z=0)

    # You (green) with a heading arrow (two fixes) + a tracking radius.
    canvas.add_player("__you__", NOW, point(0.0, 0.0))
    canvas.add_player("__you__", NOW, point(0.03, 0.02), tracking_distance=w * 0.12)
    others = [("Whitewitch", 0.14, -0.10), ("Roger the Rogue", -0.12, 0.11)]
    if extra_dots:
        others += [("Elyndra", 0.09, 0.16), ("Dagath", -0.16, -0.06), ("Borin", 0.02, -0.18)]
    for name, fx, fy in others:
        canvas.add_player(name, NOW, point(fx, fy))
    if not extra_dots:
        canvas.create_spawn_point(cx - w * 0.09, cy + h * 0.06, 420)

    from PySide6.QtWidgets import QApplication

    maps.resize(*size)
    QApplication.processEvents()
    vp = canvas.viewport().size()
    ratio = min(vp.width() / w, vp.height() / h) * 0.85
    canvas.update_(ratio)
    canvas.centerOn(cx, cy)
    QApplication.processEvents()


def _capture_tray(app) -> None:
    """Grab the real system-tray QMenu.

    The live tray handler (``NomnsParse._menu``) ends in a blocking modal
    ``menu.exec`` that wedges under the offscreen platform — and ``QMenu.exec``
    can't be intercepted from Python. So we call ``_build_tray_menu`` (the pure
    construction half, split out for exactly this) and ``popup`` it instead:
    ``popup`` is non-blocking, lays the menu out, and lets ``grab`` render it.
    """
    from PySide6.QtCore import QPoint, Qt

    menu, _actions = app._build_tray_menu()
    _keep(menu)
    # _build_tray_menu sets WA_DeleteOnClose; drop it so close() (below) doesn't
    # free the menu out from under the grab.
    menu.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
    menu.adjustSize()
    menu.popup(QPoint(0, 0))  # non-blocking (unlike exec); shows + lays out
    app.processEvents()
    app.processEvents()
    _composite_and_save(menu.grab(), "tray--menu", PANEL_BACKDROP, pad=1)
    menu.close()


def _restore_legacy(had: bool, backup: Path, legacy: Path) -> None:
    """Restore/remove the repo-root legacy config. Idempotent and never raises."""
    try:
        if had:
            shutil.copy2(backup, legacy)
        else:
            legacy.unlink(missing_ok=True)
    except OSError:
        pass


# --------------------------------------------------------------------------- #
# The README product shot — four windows composed on one canvas
# --------------------------------------------------------------------------- #
PRODUCT_SHOT = "readme--product-shot"

# Layout. The alert banner spans the top (that is where an event overlay
# actually sits over the game); maps, timers and the DPS meter share one
# row beneath it at a common height, which is what keeps the canvas wide rather
# than tall — a README renders it at roughly 900px, so height is the scarce
# axis. Sizes are the panels' own natural framings where they have one
# (timers 250 wide, DPS 300) so the product shot and the individual shots
# show the same windows at the same proportions.
#
# Each window gets its own natural height and the row is TOP-aligned rather
# than stretched to a common height: a panel padded out to a shared height
# shows a band of empty chrome that reads as a rendering fault, while unequal
# heights over the backdrop read as what they are — separate windows on a
# desktop. The banner is tall enough to lay out the timer bars under the
# alert; below roughly 200px the overlay has room for the headline only and
# the bars silently vanish from the shot.
PRODUCT_PAD = 28
PRODUCT_GAP = 20
PRODUCT_BANNER_H = 215
PRODUCT_MAPS = (760, 430)
PRODUCT_SPELL = (250, 285)
PRODUCT_DPS = (300, 310)
# Slightly darker than PANEL_BACKDROP: the panels carry their own near-opaque
# background, so the canvas reads as the gap between windows rather than as
# part of any one of them.
PRODUCT_BACKDROP = "#0c0e13"


def _product_layout() -> tuple[int, int, dict[str, tuple[int, int, int, int]]]:
    """Pure geometry for the product shot: canvas size + each window's box.

    Separated from the painting so the arrangement can be reasoned about (and
    adjusted) without booting Qt.
    """
    row_y = PRODUCT_PAD + PRODUCT_BANNER_H + PRODUCT_GAP
    boxes: dict[str, tuple[int, int, int, int]] = {}
    x = PRODUCT_PAD
    for key, (w, h) in (("maps", PRODUCT_MAPS), ("spell", PRODUCT_SPELL), ("dps", PRODUCT_DPS)):
        boxes[key] = (x, row_y, w, h)
        x += w + PRODUCT_GAP
    banner_w = x - PRODUCT_GAP - PRODUCT_PAD
    boxes["overlay"] = (PRODUCT_PAD, PRODUCT_PAD, banner_w, PRODUCT_BANNER_H)
    width = PRODUCT_PAD * 2 + banner_w
    height = row_y + max(h for _x, _y, _w, h in boxes.values() if _y == row_y) + PRODUCT_PAD
    return width, height, boxes


def _grab(widget, size: tuple[int, int]):
    """Show a window at ``size`` and grab it, alpha intact.

    Deliberately never hides afterwards — these are the full app's own
    windows, and hiding one offscreen can wedge window activation for the
    rest of the run (same reason ``capture(hide_after=False)`` exists).
    """
    from PySide6.QtWidgets import QApplication

    widget.resize(*size)
    widget.show()
    QApplication.processEvents()
    QApplication.processEvents()
    return widget.grab()


def cap_product_shot(ctx) -> None:
    """Compose the README product shot from four live windows.

    This is NOT the docs hero (``home--overview.png``). The hero is a
    photograph of the real thing — EQ running, overlays on top of it, taken by
    a human at a display — and it stays manual. This is a *product shot*: no
    game behind it, composed offscreen from the same windows and the same
    synthetic data as the individual screenshots, so it regenerates with them
    and cannot end up showing a UI that no longer ships. Keep both; they
    answer different questions and neither substitutes for the other.
    """
    import nparseplus.ui.dpswindow as dpswindow
    import nparseplus.ui.eventoverlay as eventoverlay

    width, height, boxes = _product_layout()
    backend = ctx.backend
    seed_spell_timers(backend)
    seed_fights(backend)

    spell = _keep(ctx.spell_window)
    spell._refresh_timer.stop()
    spell._flash_timer.stop()
    dps = _keep(ctx.dps_window)
    dps._refresh_timer.stop()
    overlay = _keep(ctx.event_overlay)
    maps = ctx.app.maps_window

    restore_dps = freeze_now(dpswindow)
    restore_overlay = freeze_now(eventoverlay)
    try:
        spell.refresh(now=NOW)
        dps.refresh()
        seed_alert(overlay)
        _inject_map(maps, extra_dots=True, size=boxes["maps"][2:])

        grabs = {
            key: _grab(widget, boxes[key][2:])
            for key, widget in (
                ("overlay", overlay),
                ("maps", maps),
                ("spell", spell),
                ("dps", dps),
            )
        }
    finally:
        restore_overlay()
        restore_dps()

    from PySide6.QtGui import QColor, QPainter, QPixmap

    canvas = QPixmap(width, height)
    canvas.fill(QColor(PRODUCT_BACKDROP))
    painter = QPainter(canvas)
    for key, pixmap in grabs.items():
        x, y, _w, _h = boxes[key]
        painter.drawPixmap(x, y, pixmap)
    painter.end()
    _composite_and_save(canvas, PRODUCT_SHOT, PRODUCT_BACKDROP, pad=0)


PHASE_B_SHOTS = {"window--maps", "feature--sharing-dots", "tray--menu", PRODUCT_SHOT}


def run_phase_b(only: set[str] | None) -> None:
    import json

    if only is not None and not (only & PHASE_B_SHOTS):
        return  # nothing here to build — skip the (heavy) full-app boot

    # Phase B only needs maps + tray. The legacy Discord window builds a
    # QtWebEngine view that hangs offscreen/headless; block the import so it
    # falls back to its text placeholder (discord.py handles QWebEngineView=None).
    sys.modules["PySide6.QtWebEngineWidgets"] = None

    os.chdir(REPO_ROOT)
    SCRATCH.mkdir(parents=True, exist_ok=True)
    scratch_settings = SCRATCH / "settings.json"
    _write_scratch_settings(scratch_settings)

    # Back up / stage the repo-root legacy config (live, gitignored dev state).
    legacy = REPO_ROOT / "nparse.config.json"
    backup = SCRATCH / "nparse.config.json.dev-backup"
    had = legacy.exists()
    if had:
        shutil.copy2(legacy, backup)
        assert backup.exists(), "refuse to proceed without a verified config backup"
    legacy.write_text(
        json.dumps(
            {"maps": {"last_zone": "oasis of marr", "auto_hide_menu": False, "opacity": 100}}
        ),
        encoding="utf-8",
    )

    try:
        _run_phase_b_captures(scratch_settings, only)
    finally:
        _restore_legacy(had, backup, legacy)

    sys.stdout.flush()
    os._exit(0)  # skip Qt/interpreter teardown (avoids offscreen exit segfaults)


def _run_phase_b_captures(scratch_settings: Path, only: set[str] | None) -> None:
    import nparseplus.parsers.maps.mapcanvas as mapcanvas
    from nparseplus.app import create_app
    from nparseplus.helpers import config as legacy_config

    ctx = create_app([sys.argv[0]], settings_file=scratch_settings)  # do NOT start()
    app = ctx.app
    # create_app now sets the Fusion style, palette and app sheet itself, so
    # the tray menu and every window are already dressed.
    legacy_config.APP_EXIT = True  # suppress config writes on any teardown path

    restore = freeze_now(mapcanvas)
    try:
        maps = _keep(app.maps_window)
        maps._remote_expiry_timer.stop()

        def want(name: str) -> bool:
            return only is None or name in only

        if want("window--maps"):
            _inject_map(maps, extra_dots=False)
            capture(
                maps, "window--maps", size=(900, 680), backdrop=OVERLAY_BACKDROP, hide_after=False
            )
        if want("feature--sharing-dots"):
            _inject_map(maps, extra_dots=True)
            capture(
                maps,
                "feature--sharing-dots",
                size=(900, 680),
                backdrop=OVERLAY_BACKDROP,
                hide_after=False,
            )
    finally:
        restore()

    # After the maps shots: the product shot re-sizes and re-seeds the map,
    # and it wants the busier (shared-dots) version anyway.
    if want(PRODUCT_SHOT):
        cap_product_shot(ctx)

    # The tray QMenu is built via app._build_tray_menu() + popup (never the
    # blocking exec), so it captures offscreen like everything else.
    if only is None or "tray--menu" in only:
        _capture_tray(app)


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def main() -> int:
    parser = argparse.ArgumentParser(description="Generate documentation screenshots offscreen.")
    parser.add_argument("--phase", choices=["a", "b"], help="run just one phase in-process")
    parser.add_argument("--only", help="comma-separated screenshot names to (re)generate")
    args = parser.parse_args()
    only = set(args.only.split(",")) if args.only else None

    if args.phase == "a":
        run_phase_a(only)
        return 0
    if args.phase == "b":
        run_phase_b(only)
        return 0

    # Parent: run each phase in its own process (one QApplication per process,
    # and phase B needs the NomnsParse app subclass which phase A cannot share).
    base = [sys.executable, str(Path(__file__).resolve())]
    if args.only:
        base += ["--only", args.only]

    print("phase A (isolated windows):", flush=True)
    rc = subprocess.call([*base, "--phase", "a"], cwd=REPO_ROOT)

    if only is None or (only & PHASE_B_SHOTS):
        print("phase B (maps):", flush=True)
        rc_b = subprocess.call([*base, "--phase", "b"], cwd=REPO_ROOT)
        if rc_b != 0:
            # Phase B boots the full app; a headless hiccup there shouldn't mask
            # phase A's shots. Surface it, don't fail the whole run.
            print(f"  phase B exited {rc_b} (maps may be missing)", flush=True)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
