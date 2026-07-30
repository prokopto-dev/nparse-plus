"""validate_plugin + the nparseplus-plugin CLI against fixture plugins."""

from __future__ import annotations

from pathlib import Path

from nparseplus_sdk.cli import main
from nparseplus_sdk.loading import import_plugin_module
from nparseplus_sdk.validate import validate_plugin

GOOD_PLUGIN = """
from nparseplus_sdk import NParsePlugin, PluginMeta, PluginWindowSpec


def _make_window(wctx):
    raise AssertionError("factories are not called during validation")


class Demo(NParsePlugin):
    meta = PluginMeta(id="demo", name="Demo", version="1.2.3")

    def activate(self, ctx):
        ctx.add_tick(lambda now: None)
        ctx.add_window(PluginWindowSpec(key="main", title="Demo", factory=_make_window))


def create_plugin():
    return Demo()
"""

SUSPICIOUS_PLUGIN = """
import subprocess

from nparseplus_sdk import NParsePlugin, PluginMeta


class Sneaky(NParsePlugin):
    meta = PluginMeta(id="sneaky", name="Sneaky")

    def activate(self, ctx):
        eval("1 + 1")


def create_plugin():
    return Sneaky()
"""


def write_plugin(tmp_path: Path, source: str, name: str = "plug.py") -> Path:
    path = tmp_path / name
    path.write_text(source, encoding="utf-8")
    return path


def test_good_plugin_passes(tmp_path: Path) -> None:
    report = validate_plugin(write_plugin(tmp_path, GOOD_PLUGIN))
    assert report.ok, report.errors
    assert report.meta is not None and report.meta.id == "demo"
    assert report.window_count == 1
    assert report.tick_count == 1


def test_missing_factory_fails(tmp_path: Path) -> None:
    report = validate_plugin(write_plugin(tmp_path, "x = 1\n"))
    assert not report.ok
    assert any("create_plugin" in e for e in report.errors)


def test_import_error_fails_not_crashes(tmp_path: Path) -> None:
    report = validate_plugin(write_plugin(tmp_path, "import does_not_exist_anywhere\n"))
    assert not report.ok


def test_activate_raise_fails(tmp_path: Path) -> None:
    source = GOOD_PLUGIN.replace("ctx.add_tick(lambda now: None)", "raise RuntimeError('boom')")
    report = validate_plugin(write_plugin(tmp_path, source))
    assert not report.ok
    assert any("activate" in e for e in report.errors)


def test_incompatible_sdk_range_fails(tmp_path: Path) -> None:
    source = GOOD_PLUGIN.replace(
        'PluginMeta(id="demo", name="Demo", version="1.2.3")',
        'PluginMeta(id="demo", name="Demo", requires_sdk=">=99.0")',
    )
    report = validate_plugin(write_plugin(tmp_path, source))
    assert not report.ok
    assert any("incompatible" in e for e in report.errors)


def test_min_app_version_checked_when_given(tmp_path: Path) -> None:
    source = GOOD_PLUGIN.replace(
        'PluginMeta(id="demo", name="Demo", version="1.2.3")',
        'PluginMeta(id="demo", name="Demo", min_app_version="9.0.0")',
    )
    path = write_plugin(tmp_path, source)
    assert validate_plugin(path).ok  # no app version supplied -> not checked
    assert not validate_plugin(path, app_version="1.15.0").ok


def test_suspicious_plugin_warns_but_passes(tmp_path: Path) -> None:
    report = validate_plugin(write_plugin(tmp_path, SUSPICIOUS_PLUGIN))
    assert report.ok  # advisory findings never gate the exit code
    text = "\n".join(report.warnings)
    assert "subprocess" in text
    assert "eval" in text


def test_package_plugin_with_relative_import(tmp_path: Path) -> None:
    pkg = tmp_path / "relpkg"
    pkg.mkdir()
    (pkg / "helper.py").write_text("VALUE = 7\n", encoding="utf-8")
    (pkg / "__init__.py").write_text(
        GOOD_PLUGIN.replace(
            "def create_plugin():",
            "from .helper import VALUE\n\n\ndef create_plugin():",
        ),
        encoding="utf-8",
    )
    report = validate_plugin(pkg)
    assert report.ok, report.errors
    module = import_plugin_module(pkg)
    assert module.__name__ == "nparseplus_user_plugins.relpkg"


def test_cli_exit_codes_and_output(tmp_path: Path, capsys) -> None:
    good = write_plugin(tmp_path, GOOD_PLUGIN, "good.py")
    assert main(["validate", str(good)]) == 0
    out = capsys.readouterr().out
    assert "PASS" in out

    bad = write_plugin(tmp_path, "x = 1\n", "bad.py")
    assert main(["validate", str(bad)]) == 1
    out = capsys.readouterr().out
    assert "FAIL" in out

    sneaky = write_plugin(tmp_path, SUSPICIOUS_PLUGIN, "sneaky.py")
    assert main(["validate", str(sneaky)]) == 0
    out = capsys.readouterr().out
    assert "not a security guarantee" in out


def test_cli_json_output(tmp_path: Path, capsys) -> None:
    import json

    good = write_plugin(tmp_path, GOOD_PLUGIN, "good.py")
    assert main(["validate", "--json", str(good)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["meta"]["id"] == "demo"
    assert payload["windows"] == 1
