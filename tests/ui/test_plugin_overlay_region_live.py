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
from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QLabel, QWidget

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


class _Plugin(NParsePlugin):
    meta = PluginMeta(id="broken", name="Broken", version="1.0.0", requires_sdk=">=1.5,<2")

    def __init__(self):
        self.calls = 0

    def _boom(self):
        self.calls += 1
        raise RuntimeError("nope")

    def activate(self, ctx):
        from nparseplus_sdk.ui import PluginOverlayRegion

        ctx.add_overlay_region(
            OverlayRegionSpec(
                key="main",
                title="Broken",
                factory=lambda rctx: PluginOverlayRegion(rctx),
                has_content=self._boom,
                default_anchor="sideways",
            )
        )

    def deactivate(self):
        pass


def create_plugin():
    return _Plugin()
"""

# A plugin that registers something that is not a spec at all. add_overlay_region
# appends whatever it is handed, so this is reachable at runtime.
MALFORMED_SPEC_PLUGIN = """
from nparseplus_sdk import NParsePlugin, OverlayRegionSpec, PluginMeta


class _Plugin(NParsePlugin):
    meta = PluginMeta(id="bogus", name="Bogus", version="1.0.0", requires_sdk=">=1.5,<2")

    def activate(self, ctx):
        ctx.add_overlay_region(None)
        ctx.add_overlay_region("not a spec")

    def deactivate(self):
        pass


def create_plugin():
    return _Plugin()
"""

# A factory that hands back something that is not a QWidget at all.
JUNK_PLUGIN = """
from nparseplus_sdk import NParsePlugin, OverlayRegionSpec, PluginMeta


class _Plugin(NParsePlugin):
    meta = PluginMeta(id="junk", name="Junk", version="1.0.0", requires_sdk=">=1.5,<2")

    def activate(self, ctx):
        ctx.add_overlay_region(
            OverlayRegionSpec(
                key="main",
                title="Junk",
                factory=lambda rctx: object(),
                has_content=lambda: False,
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


def wire(qtbot, tmp_path: Path, source: str, plugin_id: str, state: WindowState | None = None):
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
    overlay = EventOverlayWindow(state=_state() if state is None else state)
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


@pytest.fixture
def overlay_edit(wired):
    """The wired overlay in position mode, plus the plugin behind the region."""
    overlay, host = wired["overlay"], wired["host"]
    overlay.set_edit_mode(True)
    plugin = next(row for row in host.statuses() if row.plugin_id == "ticker").plugin
    return overlay, plugin


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


def test_a_raising_has_content_is_retired_not_merely_silenced(
    qtbot, tmp_path: Path, caplog
) -> None:
    """The first exception stops the predicate being CALLED, not just logged.

    Suppressing the log line alone would leave a permanently broken — or
    simply expensive — predicate running on the GUI thread for every overlay
    event for the rest of the session, which is the cost the guard exists to
    avoid. So the invocation count is what this asserts; the log count alone
    would pass against exactly the bug.
    """
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
        # And the predicate itself is not being run either. BROKEN_PLUGIN
        # counts its own calls, so this is the invocation count, not the log
        # count: it must not have moved across the two passes above.
        plugin = next(row for row in context["host"].statuses() if row.plugin_id == "broken").plugin
        assert plugin.calls == 1
    finally:
        context["backend"].stop()


def test_a_factory_returning_a_non_widget_is_refused_without_taking_the_rest(
    qtbot, tmp_path: Path, caplog
) -> None:
    """A malformed factory must stay one malformed factory.

    Anything non-None used to be carried past the build: ``add_region``
    then raised on ``layout()`` inside the isolation guard, the refusal path
    called ``deleteLater()`` OUTSIDE one, and that second exception aborted
    the whole of ``build_plugin_ui`` — dropping every other plugin's UI and
    the plugin manager page with it. So the type is screened where the result
    is first seen.
    """
    with caplog.at_level("WARNING"):
        context = wire(qtbot, tmp_path, JUNK_PLUGIN, "junk")
    try:
        ui, overlay = context["ui"], context["overlay"]

        assert ui.regions_by_key == {}
        assert list(overlay._region_hosts()) == ["lanes", "utility", "alert", "bars"]
        assert any("not a QWidget" in record.message for record in caplog.records)
        # It got no further: the overlay never refused it, because it was
        # never offered.
        assert not [r for r in caplog.records if "refused by the overlay" in r.message]
        # And the rest of build_plugin_ui ran — the plugin manager page is the
        # thing an abort would have cost every user.
        assert ui.extra_pages
    finally:
        context["backend"].stop()


# A spec whose key is not a str, and whose __str__ raises when interpolated.
EXPLODING_KEY_PLUGIN = PLUGIN.replace(
    '                key="main",',
    "                key=_Exploding(),",
).replace(
    "class _Plugin(NParsePlugin):",
    """class _Exploding:
    def __str__(self):
        raise RuntimeError("key __str__ boom")

    __repr__ = __str__


class _Plugin(NParsePlugin):""",
)


def test_a_non_str_region_key_is_refused_without_being_interpolated(
    qtbot, tmp_path: Path, caplog
) -> None:
    """``spec.key`` is safe to READ, and unsafe to FORMAT.

    The screen above proves the spec is an ``OverlayRegionSpec``, but the
    dataclass validates nothing, so ``key`` can still be any object — and
    building the namespaced region key interpolates it, which calls the
    plugin's ``__str__`` outside every guard. Same shape as the non-spec
    screen, one field in, so the report names the position and the type and
    never the value.
    """
    context = wire(qtbot, tmp_path, EXPLODING_KEY_PLUGIN, "ticker")
    try:
        with caplog.at_level("WARNING"):
            pass
        overlay = context["overlay"]
        # The region is dropped and the overlay keeps exactly its built-ins...
        assert list(overlay._region_hosts()) == ["lanes", "utility", "alert", "bars"]
        # ...and the sweep completed, so the plugin manager page still exists.
        assert context["ui"].extra_pages
        overlay.set_edit_mode(True)
        overlay.set_edit_mode(False)
    finally:
        context["backend"].stop()


def test_a_malformed_spec_is_refused_without_being_dereferenced(
    qtbot, tmp_path: Path, caplog
) -> None:
    """``add_overlay_region`` appends whatever it is handed, so a plugin can
    register ``None``. Reading ``spec.key`` to build the region key happened
    BEFORE any guard, so that aborted the whole startup sweep — every other
    plugin's UI and the plugin manager page with it. The screen therefore runs
    before the first attribute access, and reports the position and the type
    rather than the key, because dereferencing is the unsafe part."""
    with caplog.at_level("WARNING"):
        context = wire(qtbot, tmp_path, MALFORMED_SPEC_PLUGIN, "bogus")
    try:
        ui, overlay = context["ui"], context["overlay"]

        assert ui.regions_by_key == {}
        assert list(overlay._region_hosts()) == ["lanes", "utility", "alert", "bars"]
        # Both bad specs reported, each naming its position and type.
        reported = [r.message for r in caplog.records if "not an OverlayRegionSpec" in r.message]
        assert len(reported) == 2
        assert "NoneType" in reported[0] and "str" in reported[1]
        # The sweep finished: the plugin manager page an abort would have cost
        # every user is still there.
        assert ui.extra_pages
    finally:
        context["backend"].stop()


def test_a_refused_region_leaves_no_record_behind(qtbot, tmp_path: Path) -> None:
    """ "Refused" and "left no trace" are not the same thing.

    ``add_region`` registers the record and mints its chip BEFORE it places
    the host, so a failure during layout leaves a record whose host the caller
    then deletes — and every later visibility pass, i.e. every overlay event,
    raises out of ``_region_size``. The overlay stops working permanently, for
    one bad add-on. The failure is injected AFTER registration on purpose,
    because that is the window the rollback exists for; a validator cannot
    anticipate it.
    """
    context = wire(qtbot, tmp_path, PLUGIN, "ticker")
    try:
        overlay, host = context["overlay"], context["host"]
        host.set_enabled("ticker", False)
        original = overlay._layout_regions
        calls = {"n": 0}

        def explode() -> None:
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("layout blew up after registration")
            original()

        overlay._layout_regions = explode
        try:
            host.set_enabled("ticker", True)
        finally:
            overlay._layout_regions = original

        assert REGION_KEY not in overlay._region_hosts()
        assert REGION_KEY not in context["ui"].regions_by_key
        # And the overlay still works, which is the point.
        overlay._update_visibility()
    finally:
        context["backend"].stop()


def test_an_unusable_default_width_costs_only_the_width(qtbot, tmp_path: Path) -> None:
    """``default_width`` is the one declared size that bypasses
    ``OverlayRegion``'s pydantic validation, because the overlay wants a
    callable rather than a stored number — so it is the one a plugin can put
    anything in. Unvalidated it reached ``max(MIN_REGION_WIDTH, host_w)``
    inside the layout pass and raised there, after registration."""
    source = PLUGIN.replace("default_width=240,", 'default_width="wide",')
    context = wire(qtbot, tmp_path, source, "ticker")
    try:
        overlay = context["overlay"]

        assert REGION_KEY in overlay._region_hosts()
        # Falls back to the overlay's own default rather than the bad value.
        assert overlay._region_size(REGION_KEY, overlay._default_region(REGION_KEY))[0] >= 120
        overlay._update_visibility()
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


# -- the rest of the acceptance criteria ---------------------------------------


# A plain QWidget region with a real control in it. The factory promises a
# QWidget, not a subclass, so this is a supported shape — and it is the one
# nothing else makes input-transparent.
PLAIN_WIDGET_PLUGIN = PLUGIN.replace(
    """        from nparseplus_sdk.ui import PluginOverlayRegion

        self.region = PluginOverlayRegion(rctx)
        return self.region""",
    """        from PySide6.QtWidgets import QPushButton, QVBoxLayout, QWidget

        self.clicks = 0
        self.region = QWidget()
        layout = QVBoxLayout(self.region)
        layout.setContentsMargins(0, 0, 0, 0)
        button = QPushButton("press me")
        button.clicked.connect(self._hit)
        layout.addWidget(button)
        return self.region

    def _hit(self):
        self.clicks += 1

    def build_late_content(self):
        from PySide6.QtWidgets import QPushButton, QVBoxLayout, QWidget

        inner = QWidget()
        QVBoxLayout(inner).addWidget(QPushButton("late"))
        self.region.layout().addWidget(inner)""",
)

# sample() that returns a single widget rather than a sequence of them.
BAD_SAMPLE_PLUGIN = PLUGIN.replace(
    """        self.region = PluginOverlayRegion(rctx)
        return self.region""",
    """        class Bad(PluginOverlayRegion):
            def sample(self):
                from PySide6.QtWidgets import QLabel

                return QLabel("not a sequence")

        self.region = Bad(rctx)
        return self.region""",
)


def _unsealed(root):
    """Widgets in ``root``'s tree that could still take a click or the focus."""
    kids = [root, *root.findChildren(QWidget)]
    return (
        [w for w in kids if not w.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)],
        [w for w in kids if w.focusPolicy() != Qt.FocusPolicy.NoFocus],
    )


def test_a_plain_widget_region_is_sealed_like_the_base(qtbot, tmp_path: Path) -> None:
    """Display-only is a promise about EVERY region, not just the ones that
    used the convenience base.

    ``PluginOverlayRegion`` seals itself; a plain QWidget has nothing that
    would. Unsealed, position mode — which drops ``WindowTransparentForInput``
    so the user can drag their chrome — hands the plugin's own control a real
    click it was never written for, and makes that rectangle impossible to
    drag, because the press never falls through to the overlay's hit-test.
    """
    context = wire(qtbot, tmp_path, PLAIN_WIDGET_PLUGIN, "ticker")
    try:
        overlay = context["overlay"]
        host = overlay._region_hosts()[REGION_KEY]
        mouse, focus = _unsealed(host)
        assert not mouse, "a plain QWidget region is not input-transparent"
        assert not focus, "a plain QWidget region can still take focus"
    finally:
        context["backend"].stop()


def test_the_press_falls_through_a_plain_region_to_the_overlay(qtbot, tmp_path: Path) -> None:
    """``QWidget.childAt`` is Qt's own hit-test and it SKIPS a widget carrying
    ``WA_TransparentForMouseEvents`` — which is the mechanism, so it is what
    this asserts rather than a synthesised press.

    (``QTest.mousePress(overlay, ...)`` posts straight to the overlay and
    bypasses hit-testing entirely, so it passes with or without the seal and
    proves nothing here.)
    """
    from PySide6.QtWidgets import QPushButton

    context = wire(qtbot, tmp_path, PLAIN_WIDGET_PLUGIN, "ticker")
    try:
        overlay = context["overlay"]
        overlay.show()
        qtbot.waitExposed(overlay)
        overlay.set_edit_mode(True)
        button = overlay._region_hosts()[REGION_KEY].findChild(QPushButton)
        point = button.mapTo(overlay, button.rect().center())
        assert not isinstance(overlay.childAt(point), QPushButton)
    finally:
        context["backend"].stop()


def test_a_child_built_after_registration_is_sealed_too(qtbot, tmp_path: Path) -> None:
    """The seal has to FOLLOW the tree, not just sweep it once.

    A region that fills itself lazily is the common shape — ``sample()`` and
    the first real update both run after construction — and a filter on the
    root alone would never see a grandchild added to an existing child.
    """
    context = wire(qtbot, tmp_path, PLAIN_WIDGET_PLUGIN, "ticker")
    try:
        overlay = context["overlay"]
        plugin = next(row for row in context["host"].statuses() if row.plugin_id == "ticker").plugin
        plugin.build_late_content()
        mouse, focus = _unsealed(overlay._region_hosts()[REGION_KEY])
        assert not mouse and not focus
    finally:
        context["backend"].stop()


def test_the_seal_is_for_regions_only_and_leaves_windows_interactive(qtbot, tmp_path: Path) -> None:
    """A plugin WINDOW must stay clickable. It is a top-level window with no
    ``WindowTransparentForInput`` on it, and sealing one would make every
    control in every add-on window dead — the exact opposite of the routing
    the region constraint exists to preserve ("need clicks -> add_window")."""
    from PySide6.QtWidgets import QPushButton

    source = (
        PLAIN_WIDGET_PLUGIN.replace(
            "from nparseplus_sdk import NParsePlugin, OverlayRegionSpec, PluginMeta",
            "from nparseplus_sdk import (\n"
            "    NParsePlugin,\n    OverlayRegionSpec,\n    PluginMeta,\n    PluginWindowSpec,\n)",
        )
        .replace(
            "        ctx.add_overlay_region(",
            """        ctx.add_window(
            PluginWindowSpec(key="w", title="W", factory=self._build_window)
        )
        ctx.add_overlay_region(""",
            1,
        )
        .replace(
            "    def _build(self, rctx):",
            """    def _build_window(self, wctx):
        from PySide6.QtWidgets import QPushButton, QVBoxLayout, QWidget

        holder = QWidget()
        QVBoxLayout(holder).addWidget(QPushButton("still clickable"))
        return holder

    def _build(self, rctx):""",
            1,
        )
    )
    context = wire(qtbot, tmp_path, source, "ticker")
    try:
        window = context["ui"].windows_by_key["plugin.ticker.w"]
        button = window.findChild(QPushButton)
        assert not button.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        assert button.focusPolicy() != Qt.FocusPolicy.NoFocus
    finally:
        context["backend"].stop()


# sample() as a generator that yields a real widget and THEN raises.
MID_ITERATION_PLUGIN = PLUGIN.replace(
    """        self.region = PluginOverlayRegion(rctx)
        return self.region""",
    """        class MidIteration(PluginOverlayRegion):
            def sample(self):
                from PySide6.QtWidgets import QLabel

                def items():
                    yield QLabel("first")
                    raise RuntimeError("mid-iteration boom")

                return items()

        self.region = MidIteration(rctx)
        return self.region""",
)

# sample() returning a bare QObject alongside a real widget. QObject HAS
# deleteLater, so screening on that method let it through.
QOBJECT_SAMPLE_PLUGIN = PLUGIN.replace(
    """        self.region = PluginOverlayRegion(rctx)
        return self.region""",
    """        class Obj(PluginOverlayRegion):
            def sample(self):
                from PySide6.QtCore import QObject
                from PySide6.QtWidgets import QLabel

                return [QLabel("real"), QObject()]

        self.region = Obj(rctx)
        return self.region""",
)


# sample() returning an object whose __iter__ ITSELF raises. Deliberately not
# a generator: iter() on a generator object just hands it back without running
# any of it, so MID_ITERATION_PLUGIN exercises __next__ and never this.
HOSTILE_ITER_PLUGIN = PLUGIN.replace(
    """        self.region = PluginOverlayRegion(rctx)
        return self.region""",
    """        class Hostile:
            def __iter__(self):
                raise RuntimeError("__iter__ boom")

        class R(PluginOverlayRegion):
            def sample(self):
                return Hostile()

        self.region = R(rctx)
        return self.region""",
)


def test_a_sample_whose_iter_raises_does_not_break_position_mode(
    qtbot, tmp_path: Path, caplog
) -> None:
    """``iter()`` runs the plugin's ``__iter__``, so it is a call like any
    other and TypeError is only its "not iterable" answer.

    This is a THIRD plugin call, distinct from ``sample()`` and from
    ``list()``: it happens before the broad guard around materialisation is
    reached, so catching only TypeError here let anything else escape
    ``set_edit_mode(True)`` — i.e. position mode did not open, for every
    region and every built-in.
    """
    context = wire(qtbot, tmp_path, HOSTILE_ITER_PLUGIN, "ticker")
    try:
        overlay = context["overlay"]
        with caplog.at_level("ERROR"):
            overlay.set_edit_mode(True)
        assert any("iterator was being obtained" in r.message for r in caplog.records)
        assert overlay._preview_widgets  # the built-ins still previewed
        overlay.set_edit_mode(False)
        assert overlay._edit_mode is False
    finally:
        context["backend"].stop()


def test_a_sample_that_raises_mid_iteration_does_not_break_position_mode(
    qtbot, tmp_path: Path, caplog
) -> None:
    """Materialising the result is a SECOND call into the plugin, not one.

    A generator can yield a widget and then raise, and a custom
    ``__iter__``/``__next__`` can raise anything; narrowing the guard to
    TypeError (the non-iterable case) left every other exception escaping
    ``set_edit_mode(True)`` exactly as before.
    """
    context = wire(qtbot, tmp_path, MID_ITERATION_PLUGIN, "ticker")
    try:
        overlay = context["overlay"]
        with caplog.at_level("ERROR"):
            overlay.set_edit_mode(True)
        assert any("while being iterated" in r.message for r in caplog.records)
        assert overlay._preview_widgets  # the built-ins still previewed
        overlay.set_edit_mode(False)
    finally:
        context["backend"].stop()


def test_a_non_widget_in_a_sample_does_not_break_the_relock(qtbot, tmp_path: Path, caplog) -> None:
    """Screened on QWidget, not on ``deleteLater`` — QObject has that too.

    A bare QObject therefore reached the overlay, and ``_discard_preview``
    calls ``layout.removeWidget(item)``, which rejects a non-widget with a
    TypeError. That is raised on the way OUT of position mode, so the overlay
    never finished relocking and was left interactive over the game — a worse
    resting state than the one the entry-side guard prevents.
    """
    context = wire(qtbot, tmp_path, QOBJECT_SAMPLE_PLUGIN, "ticker")
    try:
        overlay = context["overlay"]
        with caplog.at_level("WARNING"):
            overlay.set_edit_mode(True)
        assert any("not QWidgets" in r.message and "QObject" in r.message for r in caplog.records)
        # The real widget in the same sample is kept.
        assert overlay._preview_widgets
        # And the overlay relocks, which is what the QObject used to prevent.
        overlay.set_edit_mode(False)
        assert overlay._edit_mode is False
    finally:
        context["backend"].stop()


def test_a_sample_that_is_not_a_sequence_does_not_break_position_mode(
    qtbot, tmp_path: Path, caplog
) -> None:
    """Iterating ``sample()``'s result is itself a call into the plugin's
    value, so it belongs inside the guard.

    A bare widget (or an int) raises ``TypeError`` on iteration, and this runs
    from ``_populate_preview`` during ``set_edit_mode(True)`` — so an escape
    does not cost this region its preview, it stops POSITION MODE OPENING AT
    ALL, for every region and every built-in.
    """
    context = wire(qtbot, tmp_path, BAD_SAMPLE_PLUGIN, "ticker")
    try:
        overlay = context["overlay"]
        with caplog.at_level("WARNING"):
            overlay.set_edit_mode(True)
        assert any("not a sequence of widgets" in r.message for r in caplog.records)
        # Position mode opened, and the built-ins still previewed.
        assert overlay._preview_widgets
        overlay.set_edit_mode(False)
    finally:
        context["backend"].stop()


def test_a_contributed_region_resizes_like_a_built_in(overlay_edit) -> None:
    """ "lays out, drags, resizes and persists exactly like a built-in one" —
    the drag is covered above; this is the resize half."""
    overlay, _plugin = overlay_edit
    before = overlay._region_rect(REGION_KEY)
    start = QPoint(before.right() - 1, before.bottom() - 1)

    overlay._begin_region_edit(
        REGION_KEY,
        overlay.mapToGlobal(start),
        Qt.Edge.RightEdge | Qt.Edge.BottomEdge,
    )
    overlay._apply_region_resize(QPoint(60, 40))

    stored = overlay._state.overlay_regions[REGION_KEY]
    assert stored.width == before.width() + 60
    assert stored.height == before.height() + 40
    assert overlay._region_rect(REGION_KEY).width() == before.width() + 60


def test_a_plugin_enabled_while_position_mode_is_up_is_dressed_and_sampled(wired) -> None:
    """The add half of "including while position mode is up"; the retire half
    is covered above."""
    host, overlay, ui = wired["host"], wired["overlay"], wired["ui"]
    host.set_enabled("ticker", False)
    overlay.set_edit_mode(True)

    host.set_enabled("ticker", True)

    region = ui.regions_by_key[REGION_KEY]
    assert overlay._region_titles[REGION_KEY].isVisible()
    assert "dashed" in region.styleSheet()
    assert region in overlay._preview_widgets or region.findChildren(QLabel)

    overlay.set_edit_mode(False)

    assert "dashed" not in region.styleSheet()
    assert not region.findChildren(QLabel)


def test_a_region_works_in_the_legacy_stacked_layout(qtbot, tmp_path: Path) -> None:
    """A user who has never opened position mode has ``overlay_regions=None``,
    so the hosts sit in a QVBoxLayout around two stretch items instead of
    being placed by hand. A contributed region has to land in its anchor's
    band there — appending would have put it under the timer bars (#154)."""
    context = wire(qtbot, tmp_path, PLUGIN, "ticker", state=WindowState(geometry=(0, 0, 1000, 800)))
    try:
        overlay, ui, host = context["overlay"], context["ui"], context["host"]
        region = ui.regions_by_key[REGION_KEY]
        layout = overlay._main_layout
        placed = [layout.itemAt(i).widget() for i in range(layout.count())]

        assert region in placed
        assert not region.isHidden()
        # "bottom" anchor: after the timer-bars host, not before the stretches.
        assert placed.index(region) > placed.index(overlay._bars_host)

        host.set_enabled("ticker", False)

        assert region not in [layout.itemAt(i).widget() for i in range(layout.count())]
    finally:
        context["backend"].stop()


# -- the shipped example -------------------------------------------------------

EXAMPLES = Path(__file__).resolve().parents[2] / "examples" / "plugins"


def _example_min_app_version() -> str:
    """The app release ``kill_ticker.py`` declares it needs."""
    from nparseplus_sdk.loading import import_plugin_module

    module = import_plugin_module(EXAMPLES / "kill_ticker.py")
    version = module.create_plugin().meta.min_app_version
    assert version is not None
    return version


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
    # At the app version the example itself declares it needs, read off its own
    # metadata rather than repeated here — regions live in the HOST, so the
    # example pins min_app_version and a host older than that must refuse it
    # (the test below is that half).
    supported = _example_min_app_version()
    host = PluginHost(
        settings, backend, supported, request_save=lambda: None, plugins_dir_override=directory
    )
    host.discover_and_load()
    bridge = QtEventBridge(backend.bus)
    overlay = EventOverlayWindow(state=_state())
    qtbot.addWidget(overlay)
    try:
        ui = build_plugin_ui(host, settings, supported, lambda: None, bridge, {}, overlay)
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


def test_an_app_older_than_the_example_refuses_it_cleanly(qtbot, tmp_path: Path) -> None:
    """The gap ``min_app_version`` closes, exercised end to end.

    ``requires_sdk`` is weighed against the SDK the app RESOLVED, and every
    released app declares an SDK floor rather than a pin — v2.28.1 asks for
    ``nparseplus-sdk>=1.4,<2``, so a source install of it resolves SDK 1.5
    quite legitimately once that is published. The range then passes while
    ``HostPluginContext.add_overlay_region`` does not exist on that host, and
    without this pin the plugin would fail *inside* ``activate()`` and land in
    Settings > Plugins as an error rather than as an honest "incompatible".
    """
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
    # A shipped release without the region API. Deliberately NOT described as
    # "the last" one: that claim rots every time a release is cut ahead of this
    # branch, and it already did once. Any pre-region release proves the point.
    host = PluginHost(
        settings, backend, "2.28.1", request_save=lambda: None, plugins_dir_override=directory
    )
    try:
        host.discover_and_load()

        (loaded,) = [row for row in host.statuses() if row.plugin_id == "kill-ticker"]
        assert loaded.status == "incompatible"
        assert loaded.error is not None
        assert "2.29.0-beta.2" in loaded.error
        # Refused BEFORE activate(), which is the whole point: an AttributeError
        # out of activate() reads as a broken add-on rather than an old app.
        assert "activate" not in loaded.error
    finally:
        backend.stop()
