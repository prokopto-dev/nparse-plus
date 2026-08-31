"""The SDK's half of a plugin-contributed event-overlay region (SDK 1.5, #155).

Everything here is Qt-free by construction — a region spec is DATA, its base
widget is a lazy host re-export like ``PluginWindow``, and ``validate_plugin``
runs a plugin's ``activate`` with no Qt anywhere. That is what lets the
validate CLI check a plugin that contributes a region without PySide6
installed.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

import nparseplus_sdk
from nparseplus_sdk import ui as sdk_ui
from nparseplus_sdk.cli import main
from nparseplus_sdk.validate import validate_plugin

REGION_PLUGIN = """
from nparseplus_sdk import NParsePlugin, OverlayRegionSpec, PluginMeta


def _make_region(rctx):
    raise AssertionError("factories are not called during validation")


class Demo(NParsePlugin):
    meta = PluginMeta(id="demo", name="Demo", version="1.2.3", requires_sdk=">=1.5,<2")

    def activate(self, ctx):
        ctx.add_overlay_region(
            OverlayRegionSpec(
                key="ticker",
                title="Ticker",
                factory=_make_region,
                has_content=lambda: False,
            )
        )


def create_plugin():
    return Demo()
"""


def write_plugin(tmp_path: Path, source: str, name: str = "plug.py") -> Path:
    path = tmp_path / name
    path.write_text(source, encoding="utf-8")
    return path


# -- the public surface --------------------------------------------------------


def test_the_new_names_are_public_api() -> None:
    """Additive-only: everything in ``__all__`` is under the 1.x promise."""
    assert "OverlayRegionSpec" in nparseplus_sdk.__all__
    assert "OverlayRegionContext" in nparseplus_sdk.__all__
    assert nparseplus_sdk.__version__.startswith("1.5")


def test_the_region_base_is_a_lazy_host_re_export() -> None:
    assert "PluginOverlayRegion" in dir(sdk_ui)
    assert "PluginWindow" in dir(sdk_ui)
    with pytest.raises(AttributeError):
        _ = sdk_ui.NotAThing


def test_the_ui_module_claims_no_new_public_name() -> None:
    """``skin``/``eqfiles`` publish an ``EXPORTS`` allowlist; this module
    deliberately does not, because 1.x is additive-only and a new public name
    here — a mapping where its siblings are frozensets — would be frozen."""
    assert not hasattr(sdk_ui, "EXPORTS")
    assert dir(sdk_ui) == ["PluginOverlayRegion", "PluginWindow"]


def test_importing_the_sdk_still_pulls_no_qt() -> None:
    """The whole reason ``ui`` forwards lazily: a plugin's Qt-free unit tests
    and the validate CLI must be able to import the package."""
    code = (
        "import sys\n"
        "sys.modules['PySide6'] = None\n"
        "import nparseplus_sdk\n"
        "from nparseplus_sdk import OverlayRegionSpec, OverlayRegionContext, ui\n"
        "assert 'PluginOverlayRegion' in dir(ui)\n"
        "assert not [m for m in sys.modules if m.startswith('PySide6.')]\n"
        "print('ok')\n"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0 and "ok" in result.stdout, result.stderr


# -- validation ----------------------------------------------------------------


def test_a_plugin_contributing_a_region_validates(tmp_path: Path) -> None:
    report = validate_plugin(write_plugin(tmp_path, REGION_PLUGIN))

    assert report.ok, report.errors
    assert report.region_count == 1
    assert report.window_count == 0


def test_a_bad_region_key_is_an_error(tmp_path: Path) -> None:
    source = REGION_PLUGIN.replace('key="ticker"', 'key="Not A Key"')
    report = validate_plugin(write_plugin(tmp_path, source))

    assert not report.ok
    assert any("overlay region key" in error for error in report.errors)


def test_a_duplicate_region_key_is_an_error(tmp_path: Path) -> None:
    source = REGION_PLUGIN.replace(
        "        )\n\n\ndef create_plugin",
        "        )\n"
        "        ctx.add_overlay_region(\n"
        "            OverlayRegionSpec(\n"
        '                key="ticker",\n'
        '                title="Ticker",\n'
        "                factory=_make_region,\n"
        "                has_content=lambda: False,\n"
        "            )\n"
        "        )\n\n\ndef create_plugin",
    )
    report = validate_plugin(write_plugin(tmp_path, source))

    assert not report.ok
    assert any("duplicate overlay region key" in error for error in report.errors)


def test_a_non_callable_has_content_is_an_error(tmp_path: Path) -> None:
    source = REGION_PLUGIN.replace("has_content=lambda: False", "has_content=True")
    report = validate_plugin(write_plugin(tmp_path, source))

    assert not report.ok
    assert any("has_content is not callable" in error for error in report.errors)


def test_a_window_and_a_region_may_share_a_key(tmp_path: Path) -> None:
    """They are persisted under different dicts, so the namespaces are
    separate — refusing this would be a rule with no reason behind it."""
    source = REGION_PLUGIN.replace(
        "from nparseplus_sdk import NParsePlugin, OverlayRegionSpec, PluginMeta",
        "from nparseplus_sdk import (\n"
        "    NParsePlugin,\n"
        "    OverlayRegionSpec,\n"
        "    PluginMeta,\n"
        "    PluginWindowSpec,\n"
        ")",
    ).replace(
        "        )\n\n\ndef create_plugin",
        "        )\n"
        "        ctx.add_window(\n"
        '            PluginWindowSpec(key="ticker", title="Ticker", factory=_make_region)\n'
        "        )\n\n\ndef create_plugin",
    )
    report = validate_plugin(write_plugin(tmp_path, source))

    assert report.ok, report.errors
    assert (report.window_count, report.region_count) == (1, 1)


def test_the_cli_reports_regions(tmp_path: Path, capsys) -> None:
    path = write_plugin(tmp_path, REGION_PLUGIN)

    assert main(["validate", str(path)]) == 0
    assert "1 overlay region(s)" in capsys.readouterr().out

    assert main(["validate", str(path), "--json"]) == 0
    assert '"overlay_regions": 1' in capsys.readouterr().out
