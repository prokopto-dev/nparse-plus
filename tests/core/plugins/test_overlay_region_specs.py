"""The Qt-free half of a plugin-contributed event-overlay region (#155).

``ctx.add_overlay_region`` is a one-line append exactly like ``add_window``:
the spec is DATA until the Qt layer materializes it, which is what keeps the
whole plugin subsystem importable with PySide6 poisoned. These tests pin the
spec shape, the registration/unwind pairing, and the promise that nothing in
this layer is input-related — a region is a paint surface, permanently (owner
decision on #155).
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from nparseplus.core.plugins.host import PluginHost
from nparseplus_sdk import OverlayRegionContext, OverlayRegionSpec
from nparseplus_sdk.testing import FakePluginContext

PLUGIN = """
from nparseplus_sdk import NParsePlugin, OverlayRegionSpec, PluginMeta


class _Plugin(NParsePlugin):
    meta = PluginMeta(id="ticker", name="Ticker", version="1.0.0", requires_sdk=">=1.5,<2")

    def activate(self, ctx):
        ctx.add_overlay_region(
            OverlayRegionSpec(
                key="ticker",
                title="Ticker",
                factory=lambda rctx: object(),
                has_content=lambda: True,
                default_anchor="bottom",
                default_dy=-40,
            )
        )

    def deactivate(self):
        pass


def create_plugin():
    return _Plugin()
"""


# -- the spec shape ------------------------------------------------------------


def test_the_spec_carries_nothing_input_related() -> None:
    """The owner decision on #155, asserted rather than left as intent.

    A region never receives a click: ``WindowTransparentForInput`` is a
    top-level flag with no per-child exemption. An additive-only 1.x SDK makes
    a speculative ``accepts_input`` permanent, so the field set is checked
    directly — a name matching any of these is the mistake, not a feature.
    """
    names = {field.name for field in dataclasses.fields(OverlayRegionSpec)}

    assert names == {
        "key",
        "title",
        "factory",
        "has_content",
        "default_anchor",
        "default_dx",
        "default_dy",
        "default_width",
        "default_height",
    }
    forbidden = ("input", "mouse", "click", "focus", "interactive", "hover", "wheel", "key_")
    assert not [name for name in names if any(word in name for word in forbidden)]


def test_has_content_is_required() -> None:
    """``_update_visibility`` ORs the per-region predicates, so a region with
    no opinion could never keep the overlay on screen by itself."""
    with pytest.raises(TypeError):
        OverlayRegionSpec(key="k", title="T", factory=lambda rctx: None)  # type: ignore[call-arg]


def test_the_spec_is_frozen_like_every_other_spec() -> None:
    spec = OverlayRegionSpec(key="k", title="T", factory=lambda r: None, has_content=lambda: True)
    with pytest.raises(dataclasses.FrozenInstanceError):
        spec.key = "other"  # type: ignore[misc]


def test_the_context_defaults_to_a_no_op_content_hook() -> None:
    """A region built outside the host (a plugin's own unit test) must be able
    to call ``on_content_changed`` without wiring anything."""
    rctx = OverlayRegionContext(
        settings=object(), region_key="plugin.x.y", title="Y", on_save=lambda: None
    )

    assert rctx.on_content_changed() is None
    assert rctx.bridge is None


# -- registration --------------------------------------------------------------


def test_the_fake_context_records_regions() -> None:
    ctx = FakePluginContext()
    spec = OverlayRegionSpec(key="k", title="T", factory=lambda r: None, has_content=lambda: True)

    ctx.add_overlay_region(spec)

    assert ctx.overlay_regions == [spec]


@pytest.fixture
def host(tmp_path: Path, backend, settings):
    settings.plugins.enabled = True
    directory = tmp_path / "plugins"
    directory.mkdir()
    (directory / "ticker.py").write_text(PLUGIN, encoding="utf-8")
    host = PluginHost(
        settings,
        backend,
        "2.26.2",
        request_save=lambda: None,
        plugins_dir_override=directory,
    )
    host.discover_and_load()
    return host


def test_activating_copies_the_specs_onto_the_row(host) -> None:
    host.record_consent("ticker", True)
    host.set_enabled("ticker", True)

    (loaded,) = [row for row in host.statuses() if row.plugin_id == "ticker"]
    assert loaded.status == "active"
    assert [spec.key for spec in loaded.overlay_region_specs] == ["ticker"]
    assert [spec.key for _row, spec in host.overlay_region_specs()] == ["ticker"]


def test_disabling_clears_them_again(host) -> None:
    host.record_consent("ticker", True)
    host.set_enabled("ticker", True)

    host.set_enabled("ticker", False)

    (loaded,) = [row for row in host.statuses() if row.plugin_id == "ticker"]
    assert loaded.overlay_region_specs == []
    assert host.overlay_region_specs() == []


def test_unwind_clears_the_spec_list(host) -> None:
    """``unwind`` reverses registrations, and a region spec is one — cleared
    alongside ``window_specs``/``page_specs`` so a plugin that fails partway
    through ``activate`` leaves no half-declared UI behind."""
    host.record_consent("ticker", True)
    host.set_enabled("ticker", True)
    (loaded,) = [row for row in host.statuses() if row.plugin_id == "ticker"]
    ctx = loaded.context
    assert ctx is not None and ctx.overlay_region_specs

    ctx.unwind()

    assert ctx.overlay_region_specs == []


def test_a_region_alone_counts_as_ui_for_the_teardown_warning(host, caplog) -> None:
    """``_teardown_ui`` warns when a plugin had surfaces and nobody is
    listening. A plugin whose ONLY surface is a region must count, or it is
    switched off underneath a region left drawing on the overlay."""
    host.record_consent("ticker", True)
    host.set_enabled("ticker", True)

    with caplog.at_level("WARNING"):
        host.set_enabled("ticker", False)

    assert any("overlay regions" in record.message for record in caplog.records)
