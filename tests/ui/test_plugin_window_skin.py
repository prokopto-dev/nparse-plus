"""A plugin window is skinned out of the box, and re-dresses live.

``app._apply_appearance`` already swept every plugin window duck-typing
``apply_skin`` before SDK 1.4 — the dispatch was there and there was nothing
to read, and ``PluginWindow`` defined no hook, so an add-on rendered as bare
Qt defaults next to the app's own overlays. These tests pin both halves: the
window that overrides nothing looks right under all three skins, and the one
that overrides gets called with the new skin already active.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QLabel, QVBoxLayout

from nparseplus.config.settings import Settings
from nparseplus.ui import pluginskin, skins
from nparseplus.ui.pluginwindow import PluginWindow
from nparseplus_sdk.plugin import PluginWindowContext

pytestmark = pytest.mark.qt

WINDOW_KEY = "plugin.demo-plugin.skinned"


@pytest.fixture(autouse=True)
def _restore_appearance():
    yield
    skins.set_skin(skins.DEFAULT_SKIN)
    pluginskin.use_settings(None)


class PlainWindow(PluginWindow):
    """The add-on that does nothing: no styling, no ``apply_skin``."""

    def __init__(self, wctx: PluginWindowContext) -> None:
        super().__init__(wctx)
        layout = QVBoxLayout()
        self.label = QLabel("demo content", self)
        self.label.setObjectName(pluginskin.ROW_NAME)
        layout.addWidget(self.label)
        self.setLayout(layout)


class CustomWindow(PluginWindow):
    """The add-on that wants control and composes on top of the default."""

    def __init__(self, wctx: PluginWindowContext) -> None:
        super().__init__(wctx)
        self.dressed: list[str] = []
        self.setLayout(QVBoxLayout())

    def apply_skin(self) -> None:
        super().apply_skin()
        app = pluginskin.current()
        self.dressed.append(app.name)
        self.setStyleSheet(self.styleSheet() + f"#Total {{ color: {app.heading}; }}")


class HookWindow(PluginWindow):
    """The SDK 1.4 add-on: its rules go through the hook, so the base class
    owns the whole sheet and re-assembles it per change."""

    def __init__(self, wctx: PluginWindowContext) -> None:
        super().__init__(wctx)
        self.setLayout(QVBoxLayout())
        self.restore_visibility()  # call it last: also finalizes the skin

    def skin_stylesheet(self) -> str:
        app = pluginskin.current()
        return f"#Total {{ color: {app.heading}; background: {app.gradient(app.band)}; }}"


class LateStateWindow(PluginWindow):
    """The ordinary shape: state assigned AFTER ``super().__init__()``, and
    the stylesheet hook reads it."""

    def __init__(self, wctx: PluginWindowContext, plugin: object) -> None:
        super().__init__(wctx)
        self._plugin = plugin  # assigned after super() — the whole point
        self.setLayout(QVBoxLayout())
        self.restore_visibility()

    def skin_stylesheet(self) -> str:
        return f"#Total {{ color: {self._plugin.colour}; }}"


class BrokenHookWindow(PluginWindow):
    """A hook that raises. Cosmetic code must not cost the window."""

    def __init__(self, wctx: PluginWindowContext) -> None:
        super().__init__(wctx)
        self.setLayout(QVBoxLayout())
        self.restore_visibility()

    def skin_stylesheet(self) -> str:
        raise RuntimeError("plugin bug")


class LegacyWindow(PluginWindow):
    """A PluginWindow as written against SDK 1.3: sets its own sheet in
    __init__, knows nothing about apply_skin (there was nothing to know)."""

    def __init__(self, wctx: PluginWindowContext) -> None:
        super().__init__(wctx)
        self.setLayout(QVBoxLayout())
        self.setStyleSheet("QLabel { color: #ff00ff; }")


def _window(qtbot, cls, settings: Settings | None = None):
    """``cls`` may be a PluginWindow subclass or any factory taking a wctx."""
    settings = settings if settings is not None else Settings()
    wctx = PluginWindowContext(
        settings=settings,
        window_key=WINDOW_KEY,
        title="Demo Plugin",
        default_geometry=(120, 130, 300, 200),
        on_save=lambda: None,
    )
    window = cls(wctx)
    qtbot.addWidget(window)
    return window


@pytest.mark.parametrize("skin_name", skins.SKIN_ORDER)
def test_a_window_that_overrides_nothing_is_dressed_at_construction(qtbot, skin_name: str) -> None:
    skins.set_skin(skin_name)
    window = _window(qtbot, PlainWindow)

    sheet = window.styleSheet()
    assert sheet, "an undressed plugin window is the bug SDK 1.4 fixes"
    assert sheet == pluginskin.current().overlay_stylesheet()
    assert f"#{pluginskin.ROW_NAME}" in sheet


def test_the_frame_clears_the_painted_plate(qtbot) -> None:
    """The plate is painted on the window itself (a plugin owns its layout,
    so there is no SkinPanel to wrap it in), so the content margins have to
    hold the content off it."""
    skins.set_skin("velious")
    window = _window(qtbot, PlainWindow)

    inset = pluginskin.current().frame_inset()
    assert inset > 0
    assert window.contentsMargins().left() == inset


def test_a_skin_change_re_dresses_in_place(qtbot) -> None:
    skins.set_skin("duxa")
    window = _window(qtbot, PlainWindow)
    duxa = window.styleSheet()

    skins.set_skin("velious")
    window.apply_skin()
    assert window.styleSheet() != duxa
    assert window.styleSheet() == pluginskin.current().overlay_stylesheet()
    assert window.contentsMargins().left() == pluginskin.current().frame_inset()


def test_a_font_size_change_reaches_an_open_window(qtbot) -> None:
    """Sizes are multipliers of ``general.font_size``, and Appearance applies
    it through the same sweep — so a window built at 12 must not stay at 12."""
    settings = Settings()
    settings.general.font_size = 12
    pluginskin.use_settings(settings)
    window = _window(qtbot, PlainWindow, settings)
    assert f"font-size: {skins.px(12, skins.BODY_TEXT.scale)}px" in window.styleSheet()

    settings.general.font_size = 22
    window.apply_skin()
    assert f"font-size: {skins.px(22, skins.BODY_TEXT.scale)}px" in window.styleSheet()


def test_an_override_composes_on_the_default(qtbot) -> None:
    skins.set_skin("duxa")
    window = _window(qtbot, CustomWindow)
    # Construction runs the DEFAULT dressing, not the override: it happens
    # inside super().__init__(), before the subclass has built anything.
    assert window.dressed == []
    assert window.styleSheet() == pluginskin.current().overlay_stylesheet()

    skins.set_skin("ledger")
    window.apply_skin()
    assert window.dressed == ["ledger"]
    assert window.styleSheet().startswith(pluginskin.current().overlay_stylesheet())
    assert "#Total" in window.styleSheet()


@pytest.mark.parametrize("skin_name", skins.SKIN_ORDER)
def test_the_window_paints_the_skins_own_frame(qtbot, skin_name: str) -> None:
    """Rendering it must not raise under any skin (Velious notches its
    corners, Ledger's glass is transparent), and must put ink down."""
    skins.set_skin(skin_name)
    window = _window(qtbot, PlainWindow)
    window.resize(200, 120)

    pixmap = QPixmap(200, 120)
    pixmap.fill()
    window.render(pixmap)

    image = pixmap.toImage()
    assert image.pixelColor(100, 60).name() != "#ffffff", "the plate never painted"


# -- the pre-1.4 windows that already exist ---------------------------------------


def test_a_pre_1_4_window_keeps_the_stylesheet_it_set(qtbot) -> None:
    """The regression an additive release must not ship.

    Before SDK 1.4 ``PluginWindow`` had no ``apply_skin``, so the app's
    duck-typed sweep found nothing and a plugin's own sheet survived forever.
    Inheriting one that *replaces* the sheet would silently unstyle every
    such plugin — immediately for one enabled live (the post-build appearance
    sweep runs at once), on the next skin change for one loaded at startup.
    """
    skins.set_skin("duxa")
    window = _window(qtbot, LegacyWindow)
    assert "#ff00ff" in window.styleSheet()

    skins.set_skin("velious")
    window.apply_skin()

    assert "#ff00ff" in window.styleSheet(), "the plugin's own rules were discarded"
    # Ours are refreshed underneath, and theirs come last so they still win.
    assert window.styleSheet().startswith(pluginskin.current().overlay_stylesheet())
    assert window.styleSheet().endswith("QLabel { color: #ff00ff; }")


def test_an_adopted_sheet_survives_repeated_changes_without_growing(qtbot) -> None:
    """Adopted once, re-applied thereafter — not re-adopted on top of itself."""
    window = _window(qtbot, LegacyWindow)
    for name in ("velious", "ledger", "duxa", "velious"):
        skins.set_skin(name)
        window.apply_skin()

    assert window.styleSheet().count("#ff00ff") == 1


def test_a_pre_1_4_window_that_restyles_itself_is_re_adopted(qtbot) -> None:
    """A legacy window is free to call setStyleSheet again later (its own
    refresh); the newer sheet is what gets kept."""
    window = _window(qtbot, LegacyWindow)
    skins.set_skin("velious")
    window.apply_skin()

    window.setStyleSheet("QLabel { color: #00ff00; }")
    skins.set_skin("ledger")
    window.apply_skin()

    assert "#00ff00" in window.styleSheet()
    assert "#ff00ff" not in window.styleSheet()


# -- the SDK 1.4 hook --------------------------------------------------------------


def test_the_hook_is_re_evaluated_per_change_and_never_accumulates(qtbot) -> None:
    skins.set_skin("duxa")
    window = _window(qtbot, HookWindow)
    assert window.styleSheet().count("#Total") == 1
    assert window.styleSheet() == pluginskin.current().overlay_stylesheet() + (
        window.skin_stylesheet()
    )

    for name in ("velious", "ledger", "duxa"):
        skins.set_skin(name)
        window.apply_skin()
        assert window.styleSheet().count("#Total") == 1, "the sheet grew a stale copy"
        assert pluginskin.current().gradient(pluginskin.current().band) in window.styleSheet()


def test_the_default_hook_contributes_nothing(qtbot) -> None:
    window = _window(qtbot, PlainWindow)
    window.restore_visibility()
    assert window.skin_stylesheet() == ""
    assert window.styleSheet() == pluginskin.current().overlay_stylesheet()


def test_appending_in_apply_skin_still_works_and_stays_bounded(qtbot) -> None:
    """``skin_stylesheet`` is the documented route, but a window that appends
    to ``self.styleSheet()`` in ``apply_skin`` must not grow a copy of its
    rules per change — the base strips the dressing it last wrote."""
    skins.set_skin("duxa")
    window = _window(qtbot, CustomWindow)
    for name in ("velious", "ledger", "duxa", "velious", "ledger"):
        skins.set_skin(name)
        window.apply_skin()

    assert window.styleSheet().count("#Total") <= 2


# -- the hook is never called during super().__init__() ---------------------------


def test_the_hook_may_read_state_assigned_after_super_init(qtbot) -> None:
    """The base constructor must apply only its own dressing.

    ``skin_stylesheet`` is virtual, so calling it from ``super().__init__()``
    runs it before the subclass has assigned ``self._plugin`` — and the host
    wraps the window factory in try/except and SKIPS the window on any
    exception (``pluginbootstrap``), so an AttributeError there is not a
    cosmetic failure: the add-on silently does not appear.
    """
    plugin = SimpleNamespace(colour="#abcdef")
    window = _window(qtbot, lambda wctx: LateStateWindow(wctx, plugin))

    assert "#abcdef" in window.styleSheet(), "the hook never ran after construction"
    assert window.styleSheet().startswith(pluginskin.current().overlay_stylesheet())


def test_the_hook_is_not_consulted_during_construction(qtbot) -> None:
    """Directly: a window that has not finished building has the default
    dressing and nothing else."""
    calls: list[str] = []

    class Probe(PluginWindow):
        def __init__(self, wctx: PluginWindowContext) -> None:
            super().__init__(wctx)
            # Inside the subclass constructor, before restore_visibility.
            calls.append(self.styleSheet())
            self.setLayout(QVBoxLayout())

        def skin_stylesheet(self) -> str:
            return "#Total { color: #abcdef; }"

    window = _window(qtbot, Probe)
    assert calls == [pluginskin.current().overlay_stylesheet()]
    assert "#abcdef" not in calls[0]
    # ...and the hook lands once the window is finalized.
    window.show()
    assert "#abcdef" in window.styleSheet()


def test_a_window_shown_without_restore_visibility_is_finalized(qtbot) -> None:
    """A window opened straight from the tray never called
    ``restore_visibility``; it must still be dressed before its first paint."""

    class Bare(PluginWindow):
        def __init__(self, wctx: PluginWindowContext) -> None:
            super().__init__(wctx)
            self.setLayout(QVBoxLayout())

        def skin_stylesheet(self) -> str:
            return "#Total { color: #abcdef; }"

    window = _window(qtbot, Bare)
    assert "#abcdef" not in window.styleSheet()
    window.show()
    assert "#abcdef" in window.styleSheet()


def test_a_raising_hook_costs_the_rules_not_the_window(qtbot) -> None:
    window = _window(qtbot, BrokenHookWindow)
    window.show()

    assert window.isVisible()
    assert window.styleSheet() == pluginskin.current().overlay_stylesheet()


def test_the_duck_typed_sweep_finds_the_hook(qtbot) -> None:
    """``app._apply_appearance`` calls ``apply_skin`` by name off a plain
    ``getattr`` — the coupling is the method name and nothing else."""
    window = _window(qtbot, PlainWindow)
    assert callable(getattr(window, "apply_skin", None))
