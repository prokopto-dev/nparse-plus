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

EXAMPLES = Path(__file__).resolve().parents[3] / "examples" / "plugins"


class RecordingSpeaker:
    def __init__(self) -> None:
        self.said: list[str] = []

    def speak(self, text: str) -> None:
        self.said.append(text)


def test_examples_pass_validation() -> None:
    for path in (EXAMPLES / "hello_timer.py", EXAMPLES / "merchant_prices"):
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

    class _Storage:
        def load(self):
            return {}

        def save(self, data):
            self.data = data

    class _Ctx:
        storage = _Storage()
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
