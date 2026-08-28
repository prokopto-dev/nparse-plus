"""The shipped example plugins, loaded end-to-end through a real backend."""

from __future__ import annotations

import importlib
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from nparseplus.composition import build_backend
from nparseplus.config.settings import Settings
from nparseplus.core.plugins.host import PluginHost
from nparseplus.core.timers import TimerRow
from nparseplus_sdk.loading import import_plugin_module
from nparseplus_sdk.validate import validate_plugin

from .conftest import approve

REPO_ROOT = Path(__file__).resolve().parents[3]
EXAMPLES = REPO_ROOT / "examples" / "plugins"

#: The app release that first ships ``ctx.add_overlay_region`` (#155). A
#: constant, not a comparison against ``nparseplus.__version__``: a first-
#: supporting release is a permanent fact about history, so a test that ties
#: it to whatever the tree currently reads would fail on the next unrelated
#: release and push someone into raising a floor that is already correct.
REGION_MIN_APP_VERSION = "2.28.0"


class _FakeStorage:
    def load(self):
        return {}

    def save(self, data):
        self.data = data


class RecordingSpeaker:
    def __init__(self) -> None:
        self.said: list[str] = []

    def speak(self, text: str) -> None:
        self.said.append(text)


def test_examples_pass_validation() -> None:
    for path in (
        EXAMPLES / "hello_timer.py",
        EXAMPLES / "merchant_prices",
        EXAMPLES / "tod_window.py",
        EXAMPLES / "kill_ticker.py",
    ):
        report = validate_plugin(path)
        assert report.ok, (path, report.errors)


def test_hello_timer_end_to_end() -> None:
    settings = Settings()
    settings.sharing.mode = "off"
    approve(settings, "hello-timer")
    speaker = RecordingSpeaker()
    backend = build_backend(settings, speaker=speaker)
    host = PluginHost(settings, backend, "1.15.0", plugins_dir_override=EXAMPLES)
    host.discover_and_load()
    host.activate_enabled()
    active = {p.plugin_id for p in host.statuses() if p.status == "active"}
    assert active == {"hello-timer"}  # merchant-prices stays pending consent

    backend.pipeline.process("[Wed Jul 15 12:00:00 2026] You say, 'hello nparse'")
    rows = [r for r in backend.timers.snapshot() if isinstance(r, TimerRow)]
    assert any(r.name == "Hello from a plugin" for r in rows), rows
    assert speaker.said == ["Hello from your plugin"]

    # Unrelated say lines do not fire it.
    backend.pipeline.process("[Wed Jul 15 12:00:01 2026] You say, 'hello there'")
    assert len(speaker.said) == 1


def test_merchant_prices_tracks_and_polls() -> None:
    settings = Settings()
    settings.sharing.mode = "off"
    approve(settings, "merchant-prices")
    backend = build_backend(settings, speaker=RecordingSpeaker())
    host = PluginHost(settings, backend, "1.15.0", plugins_dir_override=EXAMPLES)
    host.discover_and_load()
    host.activate_enabled()
    (merchant,) = [p for p in host.statuses() if p.plugin_id == "merchant-prices"]
    assert merchant.status == "active"
    plugin = merchant.plugin
    assert plugin is not None
    # Window + settings page declared.
    assert [spec.key for spec in merchant.window_specs] == ["prices"]
    assert [spec.title for spec in merchant.page_specs] == ["Merchant Prices"]

    backend.pipeline.process(
        "[Wed Jul 15 12:00:00 2026] You auction, "
        "'WTS Words of Crippling Force | Words of Incarceration 100pp'"
    )
    _version, rows = plugin.snapshot()
    assert [name for name, _price in rows] == [
        "Words of Crippling Force",
        "Words of Incarceration",
    ]
    assert all(price is None for _name, price in rows)

    # No server known yet -> tick must not fetch.
    class RecordingApi:
        def __init__(self) -> None:
            self.calls: list[tuple[int, list[str]]] = []

        def item_prices(self, server: int, names: list[str]):
            self.calls.append((server, names))

            class Price:
                def __init__(self, name: str) -> None:
                    self.item_name = name
                    self.total_wts_last_6_months_average = 123

            return [Price(name) for name in names]

    api = RecordingApi()
    backend.pigparse_api = api  # ctx.pigparse prefers the backend client

    class SyncWorker:
        def submit(self, fetch, apply=None):
            result = fetch()
            if apply is not None:
                apply(result)

    backend.net_worker = SyncWorker()

    now = datetime(2026, 7, 15, 12, 0, 5)
    plugin._tick(now)
    assert api.calls == []  # server unknown

    from nparseplus.core.enums import Server

    backend.player.server = Server.GREEN
    plugin._tick(now + timedelta(seconds=1))
    assert len(api.calls) == 1
    _version, rows = plugin.snapshot()
    assert all(price == 123 for _name, price in rows)

    # Throttled: an immediate second tick does not re-fetch.
    plugin._tick(now + timedelta(seconds=2))
    assert len(api.calls) == 1


def test_pricing_helpers() -> None:
    import_plugin_module(EXAMPLES / "merchant_prices")
    pricing = importlib.import_module("nparseplus_user_plugins.merchant_prices.pricing")

    assert pricing.extract_wts_items("WTS Words of Crippling Force | Words of Incarceration") == [
        "Words of Crippling Force",
        "Words of Incarceration",
    ]
    assert pricing.extract_wts_items("wts Fine Steel Long Sword 50pp, Rusty Dagger x2") == [
        "Fine Steel Long Sword",
        "Rusty Dagger",
    ]
    assert pricing.extract_wts_items("WTS Puppet Strings WTB Fungi Tunic") == ["Puppet Strings"]
    assert pricing.extract_wts_items("WTB Fungi Tunic") == []
    assert pricing.extract_wts_items("selling nothing marked") == []

    assert pricing.format_platinum(0) == "—"
    assert pricing.format_platinum(1500) == "1,500pp"

    merged = pricing.merge_tracked(["Rusty Dagger"], ["rusty dagger", "Fungi Tunic"])
    assert merged == ["Rusty Dagger", "Fungi Tunic"]


@pytest.mark.qt
def test_merchant_window_builds_and_renders(qtbot) -> None:
    from nparseplus_sdk.plugin import PluginWindowContext

    module = import_plugin_module(EXAMPLES / "merchant_prices")
    plugin = module.create_plugin()

    class _Ctx:
        storage = _FakeStorage()
        player = None
        pigparse = None

        def subscribe(self, event_type, fn):
            return lambda: None

        def add_parser(self, parser):
            pass

        def add_tick(self, fn):
            pass

        def add_window(self, spec):
            self.window_spec = spec

        def add_settings_page(self, spec):
            self.page_spec = spec

        def submit(self, fetch, apply=None):
            pass

    ctx = _Ctx()
    plugin.activate(ctx)
    plugin.track_items(["Words of Odus"])
    settings = Settings()
    wctx = PluginWindowContext(
        settings=settings,
        window_key="plugin.merchant-prices.prices",
        title="Merchant Prices",
        default_geometry=(0, 0, 340, 260),
        on_save=lambda: None,
    )
    window = ctx.window_spec.factory(wctx)
    qtbot.addWidget(window)
    assert window._table.rowCount() == 1
    assert window._table.item(0, 0).text() == "Words of Odus"
    assert window._table.item(0, 1).text() == "…"

    page = ctx.page_spec.builder(None)
    qtbot.addWidget(page)
    from PySide6.QtWidgets import QSpinBox

    spin = page.findChild(QSpinBox, "poll_seconds")
    spin.setValue(600)
    ctx.page_spec.apply(page)
    assert plugin._poll_seconds == 600


@pytest.mark.qt
def test_merchant_window_is_the_skin_facade_reference(qtbot) -> None:
    """The example is what a plugin author copies, so it has to keep working
    under every skin — including the live change, which is the whole reason
    ``nparseplus_sdk.skin`` exists rather than a page of hex in the plugin.
    """
    from nparseplus.ui import pluginskin, skins
    from nparseplus_sdk.plugin import PluginWindowContext

    module = import_plugin_module(EXAMPLES / "merchant_prices")
    plugin = module.create_plugin()

    class _Ctx:
        storage = _FakeStorage()
        player = None
        pigparse = None

        def subscribe(self, event_type, fn):
            return lambda: None

        def add_parser(self, parser):
            pass

        def add_tick(self, fn):
            pass

        def add_window(self, spec):
            self.window_spec = spec

        def add_settings_page(self, spec):
            pass

        def submit(self, fetch, apply=None):
            pass

    ctx = _Ctx()
    plugin.activate(ctx)
    plugin.track_items(["Words of Odus"])
    settings = Settings()
    wctx = PluginWindowContext(
        settings=settings,
        window_key="plugin.merchant-prices.prices",
        title="Merchant Prices",
        default_geometry=(0, 0, 340, 260),
        on_save=lambda: None,
    )

    try:
        skins.set_skin("duxa")
        window = ctx.window_spec.factory(wctx)
        qtbot.addWidget(window)
        duxa = window.styleSheet()
        _assert_selection_is_readable(window)
        # The default dressing is in there, and the example's own rules on top.
        assert duxa.startswith(pluginskin.current().overlay_stylesheet())
        assert "QHeaderView::section" in duxa
        # A title label stamped with the façade's object name, nothing else.
        assert window._title.objectName() == pluginskin.TITLE

        for name in ("velious", "ledger"):
            skins.set_skin(name)
            window.apply_skin()
            sheet = window.styleSheet()
            assert sheet != duxa
            assert sheet.startswith(pluginskin.current().overlay_stylesheet())
            # The new hue reached the example's OWN rules, not just the base
            # sheet it composed onto — and exactly once, no stale copy.
            assert sheet.count(skins.rgba(pluginskin.current().accent, 0.25)) == 1
            assert window._table.item(0, 1) is not None  # cells rebuilt, not stale
            _assert_selection_is_readable(window)
    finally:
        skins.set_skin(skins.DEFAULT_SKIN)


def _assert_selection_is_readable(window) -> None:
    """The pair the example actually paints, composited and measured.

    A selected cell is body-sized text on the skin's own band. The tempting
    foreground is the skin's caps colour — the app's config chrome uses it for
    its sidebar — but on Ledger's band that is 3.4:1, and on a naive
    ``rgba(accent, .28)`` tint 2.9:1. The example takes ``heading`` from the
    palette instead, which is the value/hue rule doing its job.
    """
    from nparseplus.ui import pluginskin

    from ...ui.test_pluginskin import composite, contrast

    app = pluginskin.current()
    assert f"color: {app.heading}" in window.styleSheet()
    assert app.gradient(app.band) in window.styleSheet()
    assert contrast(app.heading, composite(app.band[0], app.surface)) >= 4.5, app.name


def test_the_region_example_pins_the_app_release_that_supports_it() -> None:
    """``requires_sdk`` alone would let an older host accept this plugin.

    The range is weighed against the SDK the app RESOLVED, not the contract it
    IMPLEMENTS, and every released app declares an SDK floor rather than a pin
    — v2.27.0 asks for ``nparseplus-sdk>=1.4,<2``, so a plain pip/source
    install of it resolves SDK 1.5 quite legitimately once that is on PyPI.
    ``ctx.add_overlay_region`` lives in the HOST, so the range would pass and
    ``activate()`` would then raise ``AttributeError``. ``min_app_version`` is
    the one input to the handshake that comes from the host itself.

    Checked against a CONSTANT, never against ``nparseplus.__version__``. The
    pin is a permanent historical fact — "regions first shipped in 2.28.0" —
    and comparing it to the tree's own version made it a moving target: the
    correct pin would start failing the moment an unrelated 2.29 release
    landed, which either blocks that release or pressures whoever hits it into
    raising the floor and cutting off the 2.28 users it is supposed to admit.
    The value is pinned in three places by design (here, and the two documents
    below), so changing it is a deliberate edit rather than a drift.
    """
    from packaging.version import Version

    plugin = import_plugin_module(EXAMPLES / "kill_ticker.py").create_plugin()

    assert plugin.meta.requires_sdk == ">=1.5,<2"
    assert plugin.meta.min_app_version == REGION_MIN_APP_VERSION, (
        "a plugin using a host-backed API must pin the app release that shipped it "
        "— see docs/plugins/versioning.md"
    )
    Version(REGION_MIN_APP_VERSION)  # parseable, or check_compat refuses it outright


def test_the_docs_name_the_same_first_supporting_release() -> None:
    """The pin only helps third-party authors if the docs tell them the same
    number the shipped example uses. Same guard shape as the registry URL: pin
    the value once, then assert the prose that teaches it agrees."""
    for relpath in (
        "docs/plugins/versioning.md",
        "docs/plugins/overlay-regions.md",
        "docs/plugins/developing.md",
    ):
        text = (REPO_ROOT / relpath).read_text(encoding="utf-8")
        assert f'min_app_version="{REGION_MIN_APP_VERSION}"' in text, (
            f"{relpath} must name the app release that first shipped overlay regions"
        )
