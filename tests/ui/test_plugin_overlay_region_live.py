"""A contributed overlay region follows its plugin, with no restart (#155/#45).

The window seam was proved in ``test_plugin_live_ui.py``; this is the same
property for the surface that lives INSIDE the event overlay, where teardown
goes through ``EventOverlayWindow.remove_region`` rather than the layout
manager and where the region must not acquire a tray entry, a chat toggle or
a Settings > Windows row on the way — it is not a window.

The overlay here is the real ``EventOverlayWindow``, because the whole point
is that the region joins its registry, its position mode and its visibility
vote.
"""

from __future__ import annotations

import gc
import weakref
from pathlib import Path

import pytest
from PySide6.QtWidgets import QWidget

from nparseplus.audio.tts import NullSpeaker
from nparseplus.composition import build_backend
from nparseplus.config.settings import OverlayRegion, PluginEntry, Settings, WindowState
from nparseplus.core.plugins.host import PluginHost
from nparseplus.pluginbootstrap import build_plugin_ui
from nparseplus.ui.eventoverlay import EventOverlayWindow
from nparseplus.ui.qtbridge import QtEventBridge

pytestmark = pytest.mark.qt

APP_VERSION = "2.26.2"
REGION_KEY = "plugin.ticker.main"

PLUGIN = """
from nparseplus_sdk import NParsePlugin, OverlayRegionSpec, PluginMeta


class _Plugin(NParsePlugin):
    meta = PluginMeta(id="ticker", name="Ticker", version="1.0.0", requires_sdk=">=1.5,<2")

    def __init__(self):
        self.occupied = False
        self.region = None

    def activate(self, ctx):
        ctx.add_overlay_region(
            OverlayRegionSpec(
                key="main",
                title="Ticker",
                factory=self._build,
                has_content=lambda: self.occupied,
                default_anchor="bottom",
                default_dy=-40,
                default_width=240,
                default_height=48,
            )
        )

    def _build(self, rctx):
        from nparseplus_sdk.ui import PluginOverlayRegion

        self.region = PluginOverlayRegion(rctx)
        return self.region

    def deactivate(self):
        pass


def create_plugin():
    return _Plugin()
"""

# Two regions under one key — the collision _register_region has to survive.
DUPLICATE_PLUGIN = """
from nparseplus_sdk import NParsePlugin, OverlayRegionSpec, PluginMeta


def _spec():
    from nparseplus_sdk.ui import PluginOverlayRegion

    return OverlayRegionSpec(
        key="main",
        title="Twice",
        factory=lambda rctx: PluginOverlayRegion(rctx),
        has_content=lambda: False,
    )


class _Plugin(NParsePlugin):
    meta = PluginMeta(id="twice", name="Twice", version="1.0.0", requires_sdk=">=1.5,<2")

    def activate(self, ctx):
        ctx.add_overlay_region(_spec())
        ctx.add_overlay_region(_spec())

    def deactivate(self):
        pass


def create_plugin():
    return _Plugin()
"""

# A region whose has_content() raises, and whose factory is fine.
BROKEN_PLUGIN = """
from nparseplus_sdk import NParsePlugin, OverlayRegionSpec, PluginMeta


def _boom():
    raise RuntimeError("nope")


class _Plugin(NParsePlugin):
    meta = PluginMeta(id="broken", name="Broken", version="1.0.0", requires_sdk=">=1.5,<2")

    def activate(self, ctx):
        from nparseplus_sdk.ui import PluginOverlayRegion

        ctx.add_overlay_region(
            OverlayRegionSpec(
                key="main",
                title="Broken",
                factory=lambda rctx: PluginOverlayRegion(rctx),
                has_content=_boom,
                default_anchor="sideways",
            )
        )

    def deactivate(self):
        pass


def create_plugin():
    return _Plugin()
"""


class FakeTray:
    def __init__(self) -> None:
        self.windows: dict[str, object] = {}

    def add_backend_window(self, label: str, window: object) -> None:
        self.windows[label] = window

    def remove_backend_window(self, label: str) -> bool:
        return self.windows.pop(label, None) is not None

    def has_backend_window(self, label: str) -> bool:
        return label in self.windows


class FakeSettingsWindow:
    def __init__(self) -> None:
        self.pages: list[object] = []
        self.rows: list[tuple] = []

    def add_page(self, spec) -> None:
        self.pages.append(spec)

    def remove_page(self, spec) -> None:
        if spec in self.pages:
            self.pages.remove(spec)

    def set_plugin_window_rows(self, rows) -> None:
        self.rows = list(rows)


class FakeLayouts:
    def __init__(self) -> None:
        self.windows: dict[str, object] = {}

    def add_window(self, key: str, widget: object) -> None:
        self.windows[key] = widget

    def remove_window(self, key: str) -> bool:
        return self.windows.pop(key, None) is not None


def _state() -> WindowState:
    return WindowState(
        geometry=(0, 0, 1000, 800),
        overlay_regions={
            "lanes": OverlayRegion(anchor="top"),
            "utility": OverlayRegion(anchor="top", dy=96),
            "alert": OverlayRegion(anchor="center"),
            "bars": OverlayRegion(anchor="bottom"),
        },
    )


def wire(qtbot, tmp_path: Path, source: str, plugin_id: str):
    """``create_app``'s assembly, reduced to what a region has to keep in step."""
    directory = tmp_path / "plugins"
    directory.mkdir()
    (directory / f"{plugin_id}.py").write_text(source, encoding="utf-8")

    settings = Settings()
    settings.sharing.mode = "off"
    settings.mobinfo.wiki_details = False
    settings.plugins.enabled = True
    settings.plugins.update_check = False
    settings.plugins.entries[plugin_id] = PluginEntry(enabled=True, approved=True)

    backend = build_backend(settings, speaker=NullSpeaker())
    host = PluginHost(
        settings,
        backend,
        APP_VERSION,
        request_save=lambda: None,
        plugins_dir_override=directory,
    )
    host.discover_and_load()
    bridge = QtEventBridge(backend.bus)
    overlay = EventOverlayWindow(state=_state())
    qtbot.addWidget(overlay)
    window_handles: dict[str, object] = {}
    ui = build_plugin_ui(host, settings, APP_VERSION, lambda: None, bridge, window_handles, overlay)
    chrome_surfaces: list[object] = []
    dressed: list[int] = []
    ui.attach_live(
        plugin_host=host,
        settings=settings,
        save=lambda: None,
        bridge=bridge,
        window_handles=window_handles,
        settings_window=FakeSettingsWindow(),
        layouts=FakeLayouts(),
        legacy_app=FakeTray(),
        chrome_surfaces=chrome_surfaces,
        apply_appearance=lambda: dressed.append(1),
        event_overlay=overlay,
    )
    return {
        "host": host,
        "ui": ui,
        "overlay": overlay,
        "settings": settings,
        "backend": backend,
        "chrome_surfaces": chrome_surfaces,
        "window_handles": window_handles,
    }


@pytest.fixture
def wired(qtbot, tmp_path: Path):
    context = wire(qtbot, tmp_path, PLUGIN, "ticker")
    yield context
    context["backend"].stop()


# -- startup -------------------------------------------------------------------


def test_the_region_joins_the_overlay_at_launch(wired) -> None:
    overlay, ui = wired["overlay"], wired["ui"]

    assert REGION_KEY in ui.regions_by_key
    assert REGION_KEY in overlay._region_hosts()
    assert overlay._region_titles[REGION_KEY].text() == "Ticker"


def test_the_declared_defaults_reach_the_overlay(wired) -> None:
    overlay = wired["overlay"]

    default = overlay._default_region(REGION_KEY)
    assert (default.anchor, default.dy, default.height) == ("bottom", -40, 48)
    # ``default_width`` is a callable on the record, not a stored width: the
    # user's own dragged width is what lives in ``overlay_regions``.
    assert default.width is None
    assert overlay._region_size(REGION_KEY, default)[0] == 240


def test_a_region_is_not_a_window(wired) -> None:
    """No tray entry, no chat toggle, no Settings > Windows row — a region
    lives inside the overlay and is placed from the overlay's position mode."""
    ui = wired["ui"]

    assert ui.windows_by_key == {}
    assert ui.tray == {}
    assert ui.command_handles == {}
    assert ui.window_rows == []
    assert wired["window_handles"] == {}


def test_the_region_is_swept_by_a_later_skin_change(wired) -> None:
    """``create_app`` fills ``chrome_surfaces`` from ``layout_windows``, which
    a region is deliberately not in — so ``attach_live`` adopts it, or the
    region would keep the skin it launched with forever."""
    assert wired["ui"].regions_by_key[REGION_KEY] in wired["chrome_surfaces"]


# -- live ----------------------------------------------------------------------


def test_disabling_the_plugin_takes_the_region_off_the_overlay(wired) -> None:
    host, overlay, ui = wired["host"], wired["overlay"], wired["ui"]
    widget = ui.regions_by_key[REGION_KEY]

    host.set_enabled("ticker", False)

    assert REGION_KEY not in ui.regions_by_key
    assert REGION_KEY not in overlay._region_hosts()
    assert widget not in wired["chrome_surfaces"]
    assert widget.isHidden()


def test_enabling_it_again_builds_a_fresh_region(wired) -> None:
    host, overlay, ui = wired["host"], wired["overlay"], wired["ui"]
    first = ui.regions_by_key[REGION_KEY]
    host.set_enabled("ticker", False)

    host.set_enabled("ticker", True)

    assert REGION_KEY in overlay._region_hosts()
    assert ui.regions_by_key[REGION_KEY] is not first
    assert ui.regions_by_key[REGION_KEY] in wired["chrome_surfaces"]


def test_the_persisted_placement_outlives_the_plugin(wired) -> None:
    """Deliberate: a plugin disabled and re-enabled comes back where the user
    put it. Nothing prunes ``overlay_regions``, and that is the feature."""
    overlay, host = wired["overlay"], wired["host"]
    overlay._begin_region_edit(REGION_KEY, overlay.mapToGlobal(overlay.rect().center()))
    overlay._state.overlay_regions[REGION_KEY].dx = 133

    host.set_enabled("ticker", False)

    assert overlay._state.overlay_regions[REGION_KEY].dx == 133

    host.set_enabled("ticker", True)

    assert overlay._region_for(REGION_KEY).dx == 133


def test_a_region_can_be_retired_while_position_mode_is_up(wired) -> None:
    """Position mode is the one time the overlay is clickable, so it is also
    the one time a region can vanish under a drag in flight (#154)."""
    overlay, host = wired["overlay"], wired["host"]
    overlay.set_edit_mode(True)
    assert overlay._region_titles[REGION_KEY].isVisible()

    host.set_enabled("ticker", False)

    assert REGION_KEY not in overlay._region_titles
    assert overlay._drag_region is None


def test_the_region_votes_on_whether_the_overlay_is_shown(wired) -> None:
    overlay, host = wired["overlay"], wired["host"]
    plugin = next(row for row in host.statuses() if row.plugin_id == "ticker").plugin

    assert not overlay.isVisible()

    plugin.occupied = True
    plugin.region.notify_content_changed()

    assert overlay.isVisible()

    plugin.occupied = False
    plugin.region.notify_content_changed()

    assert not overlay.isVisible()


# -- misbehaving plugins -------------------------------------------------------


def test_a_duplicate_region_key_keeps_the_first(qtbot, tmp_path: Path, caplog) -> None:
    with caplog.at_level("WARNING"):
        context = wire(qtbot, tmp_path, DUPLICATE_PLUGIN, "twice")
    try:
        assert list(context["ui"].regions_by_key) == ["plugin.twice.main"]
        assert any(
            "twice" in record.message and "main" in record.message for record in caplog.records
        )
    finally:
        context["backend"].stop()


def test_a_raising_has_content_is_treated_as_empty(qtbot, tmp_path: Path, caplog) -> None:
    """Asked on every visibility pass, so it is logged once and answers False
    thereafter — a region that cannot say whether it has anything is not a
    reason to keep an always-on-top window over the game."""
    with caplog.at_level("ERROR"):
        context = wire(qtbot, tmp_path, BROKEN_PLUGIN, "broken")
        # Registering the region already runs a visibility pass, so the one
        # line the guard is allowed has been spent by now.
        first = sum("has_content() raised" in r.message for r in caplog.records)
        caplog.clear()
        overlay = context["overlay"]
        overlay._update_visibility()
        overlay._update_visibility()
        later = sum("has_content() raised" in r.message for r in caplog.records)
    try:
        assert first == 1
        assert later == 0  # not one per pass, and a pass is every overlay event
        assert not overlay.isVisible()
    finally:
        context["backend"].stop()


def test_an_unusable_default_anchor_costs_only_the_placement(qtbot, tmp_path: Path) -> None:
    """``default_anchor`` is a Literal and a plugin can pass anything; the
    region still lands, at the overlay's own default."""
    context = wire(qtbot, tmp_path, BROKEN_PLUGIN, "broken")
    try:
        overlay = context["overlay"]
        assert "plugin.broken.main" in overlay._region_hosts()
        assert overlay._default_region("plugin.broken.main").anchor == "top"
    finally:
        context["backend"].stop()


def test_no_overlay_means_the_regions_are_dropped_loudly(qtbot, tmp_path: Path, caplog) -> None:
    """An embedder without an event overlay is a legitimate configuration; a
    plugin silently missing half its UI is not."""
    directory = tmp_path / "plugins"
    directory.mkdir()
    (directory / "ticker.py").write_text(PLUGIN, encoding="utf-8")
    settings = Settings()
    settings.sharing.mode = "off"
    settings.mobinfo.wiki_details = False
    settings.plugins.enabled = True
    settings.plugins.update_check = False
    settings.plugins.entries["ticker"] = PluginEntry(enabled=True, approved=True)
    backend = build_backend(settings, speaker=NullSpeaker())
    host = PluginHost(
        settings, backend, APP_VERSION, request_save=lambda: None, plugins_dir_override=directory
    )
    host.discover_and_load()
    try:
        with caplog.at_level("WARNING"):
            ui = build_plugin_ui(
                host, settings, APP_VERSION, lambda: None, QtEventBridge(backend.bus), {}
            )

        assert ui.regions_by_key == {}
        assert any("no event overlay" in record.message for record in caplog.records)
    finally:
        backend.stop()


def test_a_widget_that_is_not_a_region_base_still_works(qtbot, tmp_path: Path) -> None:
    """``OverlayRegionSpec.factory`` promises a QWidget, not a subclass — the
    base is a convenience, and a plain widget must not be refused."""
    source = PLUGIN.replace(
        """        from nparseplus_sdk.ui import PluginOverlayRegion

        self.region = PluginOverlayRegion(rctx)
        return self.region""",
        """        from PySide6.QtWidgets import QWidget

        self.region = QWidget()
        return self.region""",
    )
    context = wire(qtbot, tmp_path, source, "ticker")
    try:
        overlay = context["overlay"]
        assert isinstance(overlay._region_hosts()[REGION_KEY], QWidget)
        # No ``sample()``, so no position-mode content — which is exactly how
        # a region with no preview factory already behaved.
        overlay.set_edit_mode(True)
        assert overlay._preview_widgets
    finally:
        context["backend"].stop()


# -- the shipped example -------------------------------------------------------

EXAMPLES = Path(__file__).resolve().parents[2] / "examples" / "plugins"


def test_the_kill_ticker_example_draws_in_the_real_overlay(qtbot, tmp_path: Path) -> None:
    """The reference add-on, end to end: it goes on the overlay, brings the
    overlay on screen when something dies, and takes it away again on a zone."""
    from datetime import datetime

    from nparseplus.core.events import SlainEvent, YouZonedEvent

    # The shipped file, alone in its own folder: ``build_plugin_ui`` runs the
    # consent prompts, and the other examples would each raise a modal one.
    directory = tmp_path / "plugins"
    directory.mkdir()
    (directory / "kill_ticker.py").write_text(
        (EXAMPLES / "kill_ticker.py").read_text(encoding="utf-8"), encoding="utf-8"
    )

    settings = Settings()
    settings.sharing.mode = "off"
    settings.mobinfo.wiki_details = False
    settings.plugins.enabled = True
    settings.plugins.update_check = False
    settings.plugins.entries["kill-ticker"] = PluginEntry(enabled=True, approved=True)

    backend = build_backend(settings, speaker=NullSpeaker())
    host = PluginHost(
        settings, backend, APP_VERSION, request_save=lambda: None, plugins_dir_override=directory
    )
    host.discover_and_load()
    bridge = QtEventBridge(backend.bus)
    overlay = EventOverlayWindow(state=_state())
    qtbot.addWidget(overlay)
    try:
        ui = build_plugin_ui(host, settings, APP_VERSION, lambda: None, bridge, {}, overlay)
        key = "plugin.kill-ticker.kills"
        assert key in ui.regions_by_key
        assert not overlay.isVisible()

        now = datetime(2026, 7, 15, 12, 0, 0)
        bridge.event_received.emit(SlainEvent(timestamp=now, raw="", victim="a froglok tad"))

        region = ui.regions_by_key[key]
        assert region.kills == ["a froglok tad"]
        assert overlay.isVisible()

        bridge.event_received.emit(
            YouZonedEvent(timestamp=now, raw="", long_name="Sebilis", short_name="sebilis")
        )

        assert region.kills == []
        assert not overlay.isVisible()
    finally:
        backend.stop()


# -- lifetime ------------------------------------------------------------------


def test_a_contributed_region_does_not_put_the_overlay_in_a_cycle(qapp, tmp_path: Path) -> None:
    """The #154 guard, from the one direction that could reintroduce it.

    The overlay owns the region's host widget, the widget holds its
    ``OverlayRegionContext``, and the context holds the content hook — so a
    hook closing over the overlay strongly is a Python reference cycle through
    the WINDOW. That hands its destruction to the cyclic collector, and a
    QWidget freed there rather than by Qt is a use-after-free the next repaint
    walks into; it segfaulted this suite from inside ``paintEvent`` before
    #154. ``_region_content_hook`` holds a ``WeakMethod`` for exactly this,
    which is why the assertion runs with the collector switched off.

    Built without the qtbot fixture on purpose: pytest-qt would hold the
    widgets it is asked to track.
    """
    from nparseplus.pluginbootstrap import _region_content_hook
    from nparseplus.ui.pluginregion import PluginOverlayRegion
    from nparseplus_sdk.plugin import OverlayRegionContext

    gc.disable()
    try:
        overlay = EventOverlayWindow(state=_state())
        rctx = OverlayRegionContext(
            settings=Settings(),
            region_key=REGION_KEY,
            title="Ticker",
            on_save=lambda: None,
            on_content_changed=_region_content_hook(overlay, REGION_KEY),
        )
        widget = PluginOverlayRegion(rctx)
        overlay.add_region(
            REGION_KEY, widget, title="Ticker", has_content=lambda: False, preview=widget.sample
        )
        # The hook still works while the overlay is alive — a weak reference
        # that is never resolvable would pass the assertion below for the
        # wrong reason.
        overlay._state.overlay_regions[REGION_KEY] = OverlayRegion(anchor="top", dy=222)
        widget.notify_content_changed()
        assert widget.pos().y() == overlay._region_rect(REGION_KEY).y()

        ref = weakref.ref(overlay)
        del overlay, widget, rctx

        assert ref() is None
    finally:
        gc.enable()
