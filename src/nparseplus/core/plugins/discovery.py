"""Finding plugins: the user plugins directory + pip entry points.

Directory sources come first (sorted by name), then entry points (sorted by
name) — the drop-in directory is the mechanism that works in the frozen
DMG/zip/Flatpak builds, where no site-packages is on ``sys.path`` and
``importlib.metadata`` sees nothing. ``PluginSource.load`` is deferred: no
plugin code runs until the host decides to load that source.
"""

from __future__ import annotations

import importlib.metadata
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from nparseplus_sdk.loading import load_plugin_factory

ENTRY_POINT_GROUP = "nparseplus.plugins"

# Installer work areas inside the plugins dir that must never load as plugins.
RESERVED_DIR_NAMES = {"trash"}


@dataclass(frozen=True)
class PluginSource:
    """One place a plugin can be loaded from (identity known pre-import)."""

    origin: Literal["dir", "entry_point"]
    name: str  # file/dir stem or entry-point name
    location: str  # path on disk, or "dist:entry-point" for entry points
    load: Callable[[], Callable[[], object]]  # -> the create_plugin factory; may raise


def discover_dir_plugins(plugins_dir: Path) -> list[PluginSource]:
    """Enumerate ``*.py`` files and package dirs in the plugins directory."""
    if not plugins_dir.is_dir():
        return []
    sources: list[PluginSource] = []
    for path in sorted(plugins_dir.iterdir(), key=lambda p: p.name):
        if path.name.startswith(("_", ".")) or path.name in RESERVED_DIR_NAMES:
            continue
        is_module = path.is_file() and path.suffix == ".py"
        is_package = path.is_dir() and (path / "__init__.py").is_file()
        if not (is_module or is_package):
            continue

        def _load(p: Path = path) -> Callable[[], object]:
            # Default arg binds the loop variable; import only happens here.
            return load_plugin_factory(p)

        sources.append(
            PluginSource(
                origin="dir",
                name=path.stem if is_module else path.name,
                location=str(path),
                load=_load,
            )
        )
    return sources


def discover_entry_point_plugins() -> list[PluginSource]:
    """Enumerate ``nparseplus.plugins`` entry points (source installs only).

    Under a frozen build there is no distribution metadata, so this returns
    ``[]`` without special-casing.
    """
    try:
        eps = importlib.metadata.entry_points(group=ENTRY_POINT_GROUP)
    except Exception:  # defensive: malformed dist metadata must not break startup
        return []
    sources: list[PluginSource] = []
    for ep in sorted(eps, key=lambda e: e.name):
        dist = ep.dist.name if ep.dist is not None else "?"
        sources.append(
            PluginSource(
                origin="entry_point",
                name=ep.name,
                location=f"{dist}:{ep.name}",
                load=ep.load,
            )
        )
    return sources


def discover_all(plugins_dir: Path) -> list[PluginSource]:
    return [*discover_dir_plugins(plugins_dir), *discover_entry_point_plugins()]
