"""A plugin window is skinned out of the box, and re-dresses live.

``app._apply_appearance`` already swept every plugin window duck-typing
``apply_skin`` before SDK 1.4 — the dispatch was there and there was nothing
to read, and ``PluginWindow`` defined no hook, so an add-on rendered as bare
Qt defaults next to the app's own overlays. These tests pin both halves: the
window that overrides nothing looks right under all three skins, and the one
that overrides gets called with the new skin already active.
"""

from __future__ import annotations

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


def _window(qtbot, cls, settings: Settings | None = None):
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


def test_the_duck_typed_sweep_finds_the_hook(qtbot) -> None:
    """``app._apply_appearance`` calls ``apply_skin`` by name off a plain
    ``getattr`` — the coupling is the method name and nothing else."""
    window = _window(qtbot, PlainWindow)
    assert callable(getattr(window, "apply_skin", None))
