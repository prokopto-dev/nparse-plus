"""Discovery: the plugins directory sweep + entry-point enumeration."""

from __future__ import annotations

import sys
from pathlib import Path

from nparseplus.core.plugins import discovery
from nparseplus.core.plugins.discovery import (
    discover_dir_plugins,
    discover_entry_point_plugins,
)

from .conftest import write_plugin


def test_missing_dir_is_empty(tmp_path: Path) -> None:
    assert discover_dir_plugins(tmp_path / "nope") == []


def test_files_and_packages_discovered_sorted(tmp_path: Path) -> None:
    write_plugin(tmp_path, "zeta.py", plugin_id="zeta")
    pkg = tmp_path / "alpha"
    pkg.mkdir()
    write_plugin(pkg, "__init__.py", plugin_id="alpha")
    sources = discover_dir_plugins(tmp_path)
    assert [(s.origin, s.name) for s in sources] == [("dir", "alpha"), ("dir", "zeta")]


def test_ignored_entries(tmp_path: Path) -> None:
    write_plugin(tmp_path, "_private.py", plugin_id="private")
    write_plugin(tmp_path, ".hidden.py", plugin_id="hidden")
    (tmp_path / "README.md").write_text("not a plugin", encoding="utf-8")
    (tmp_path / "not_a_package").mkdir()  # dir without __init__.py
    trash = tmp_path / "trash"
    trash.mkdir()
    write_plugin(trash, "old.py", plugin_id="old")
    assert discover_dir_plugins(tmp_path) == []


def test_load_is_deferred_and_namespaced(tmp_path: Path) -> None:
    write_plugin(tmp_path, "lazy.py", plugin_id="lazy")
    (source,) = discover_dir_plugins(tmp_path)
    assert "nparseplus_user_plugins.lazy" not in sys.modules
    factory = source.load()
    assert "nparseplus_user_plugins.lazy" in sys.modules
    plugin = factory()
    assert plugin.meta.id == "lazy"


def test_package_plugin_relative_import(tmp_path: Path) -> None:
    pkg = tmp_path / "relative"
    pkg.mkdir()
    (pkg / "helper.py").write_text("MAGIC = 41\n", encoding="utf-8")
    write_plugin(
        pkg,
        "__init__.py",
        plugin_id="relative",
        activate_body="        from .helper import MAGIC\n        assert MAGIC == 41",
    )
    (source,) = discover_dir_plugins(tmp_path)
    plugin = source.load()()
    plugin.activate(None)  # exercises the relative import


def test_entry_points_discovered(monkeypatch) -> None:
    class FakeEp:
        def __init__(self, name: str) -> None:
            self.name = name
            self.dist = None

        def load(self):
            return lambda: None

    monkeypatch.setattr(
        discovery.importlib.metadata,
        "entry_points",
        lambda group: [FakeEp("bravo"), FakeEp("alpha")] if group == "nparseplus.plugins" else [],
    )
    sources = discover_entry_point_plugins()
    assert [(s.origin, s.name) for s in sources] == [
        ("entry_point", "alpha"),
        ("entry_point", "bravo"),
    ]


def test_entry_point_enumeration_failure_is_isolated(monkeypatch) -> None:
    def _boom(group: str):
        raise RuntimeError("corrupt dist metadata")

    monkeypatch.setattr(discovery.importlib.metadata, "entry_points", _boom)
    assert discover_entry_point_plugins() == []
