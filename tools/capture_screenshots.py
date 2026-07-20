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
    from nparseplus.ui import theme

    settings = Settings()
    settings.general.theme = "dark"
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
    theme.set_theme("dark")
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


def cap_spell_timers(backend) -> None:
    from nparseplus.core.timers import (
        TRIGGER_TIMER_GROUP,
        YOU_GROUP,
        SpellRow,
        TimerRow,
    )
    from nparseplus.ui.spellwindow import SpellTimerWindow

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

    w = _keep(SpellTimerWindow(backend))
    w._refresh_timer.stop()
    w._flash_timer.stop()
    w.refresh(now=NOW)
    capture(w, "window--spell-timers", size=(250, 430))


def cap_dps_meter(backend) -> None:
    import nparseplus.ui.dpswindow as dpswindow
    from nparseplus.core.events import DamageEvent
    from nparseplus.ui.dpswindow import DpsMeterWindow

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


def cap_event_overlay() -> None:
    import nparseplus.ui.eventoverlay as eventoverlay
    from nparseplus.config.settings import WindowState
    from nparseplus.core.events import OverlayEvent, TimerBarEvent
    from nparseplus.ui.eventoverlay import EventOverlayWindow

    restore = freeze_now(eventoverlay)
    try:
        w = _keep(EventOverlayWindow(state=WindowState(geometry=(0, 0, 820, 460))))
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
    pages = [
        (0, "settings--overview"),
        (0, "settings--general"),
        (1, "settings--character"),
        (2, "settings--friends"),
        (3, "settings--spell-timers"),
        (4, "settings--maps"),
        (5, "settings--windows"),
        (6, "settings--audio-overlays"),
        (7, "settings--sharing"),
        (8, "settings--advanced"),
    ]
    for idx, name in pages:
        w._sidebar.setCurrentRow(idx)
        capture(w, name, size=(700, 800))


def _spell_window(backend):
    """A SpellTimerWindow with its live timers stopped, ready to grab."""
    from nparseplus.ui.spellwindow import SpellTimerWindow

    w = _keep(SpellTimerWindow(backend))
    w._refresh_timer.stop()
    w._flash_timer.stop()
    return w


def cap_spell_timers_raid(backend) -> None:
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
        capture(w, "window--spell-timers-raid", size=(250, 300))
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

    release = ReleaseInfo(
        version="1.12.0",
        html_url="https://github.com/prokopto-dev/nparse-plus/releases/tag/v1.12.0",
        notes=(
            ReleaseNote(
                version="1.12.0",
                body="- feat: x86_64 (Intel) macOS build\n"
                '- feat: "Check for updates" tray action\n'
                "- docs: offscreen screenshot generator",
            ),
            ReleaseNote(
                version="1.11.0",
                body="- Raid-mode grouping, post-expiry rebuff alerts, CH cadence indicator\n"
                '- Version badge + "Check now" in Settings',
            ),
        ),
    )
    w = _keep(UpdateAvailableDialog(release, installed_version="1.10.0"))
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
    "window--spell-timers": lambda b, s: cap_spell_timers(b),
    "window--spell-timers-raid": lambda b, s: cap_spell_timers_raid(b),
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
    "settings": lambda b, s: cap_settings(b, s),  # emits all settings--*.png
}


def _apply_dark_palette(app) -> None:
    """A dark Fusion palette so the standard-widget windows (settings, trigger
    editor) render dark offscreen. The app itself sets no palette — on a real
    machine those windows inherit the OS theme (dark on a dark desktop); this
    reproduces that deterministically for the docs, which want the dark theme.
    """
    from PySide6.QtGui import QColor, QPalette

    app.setStyle("Fusion")
    window = QColor("#2d2f36")
    base = QColor("#1e2026")
    text = QColor("#dddddd")
    disabled = QColor("#7a7d85")
    accent = QColor("#3584e4")
    pal = QPalette()
    for role, color in (
        (QPalette.ColorRole.Window, window),
        (QPalette.ColorRole.WindowText, text),
        (QPalette.ColorRole.Base, base),
        (QPalette.ColorRole.AlternateBase, window),
        (QPalette.ColorRole.Text, text),
        (QPalette.ColorRole.Button, window),
        (QPalette.ColorRole.ButtonText, text),
        (QPalette.ColorRole.ToolTipBase, base),
        (QPalette.ColorRole.ToolTipText, text),
        (QPalette.ColorRole.PlaceholderText, disabled),
        (QPalette.ColorRole.Highlight, accent),
        (QPalette.ColorRole.HighlightedText, QColor("#ffffff")),
    ):
        pal.setColor(role, color)
    for role in (
        QPalette.ColorRole.Text,
        QPalette.ColorRole.ButtonText,
        QPalette.ColorRole.WindowText,
    ):
        pal.setColor(QPalette.ColorGroup.Disabled, role, disabled)
    app.setPalette(pal)


def _apply_app_chrome(app) -> None:
    """Dark palette + the app's global QSS + fonts, so standard-widget windows
    (settings, trigger editor) render in the dark theme like app.create_app."""
    from PySide6.QtGui import QFontDatabase

    from nparseplus.ui import theme

    _apply_dark_palette(app)
    css_path = REPO_ROOT / "data" / "ui" / theme.stylesheet_filename()
    app.setStyleSheet(css_path.read_text(encoding="utf-8"))
    for font in ("NotoSans-Regular.ttf", "NotoSans-Bold.ttf"):
        path = REPO_ROOT / "data" / "fonts" / font
        if path.is_file():
            QFontDatabase.addApplicationFont(str(path))


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
    s.general.theme = "dark"
    s.general.update_check = False
    s.general.eq_log_dir = SCRATCH
    s.sharing.mode = "off"
    s.players.append(
        PlayerInfo(name="Xantik", server="green", player_class=int(PlayerClass.CLERIC), level=60)
    )
    save_settings(s, path)


def _inject_map(maps, extra_dots: bool):
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

    maps.resize(900, 680)
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


PHASE_B_SHOTS = {"window--maps", "feature--sharing-dots", "tray--menu"}


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
    _apply_dark_palette(app)  # dark tray menu (the app sets no palette itself)
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
