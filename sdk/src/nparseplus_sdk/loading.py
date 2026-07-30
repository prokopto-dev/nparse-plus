"""Importing a plugin from a path — shared by the host app and the CLI.

Plugins import under the ``nparseplus_user_plugins.<stem>`` namespace via
``spec_from_file_location`` rather than a ``sys.path`` insertion, so a stray
``httpx.py`` in the plugins folder can never shadow the app's dependencies.
Package plugins get ``submodule_search_locations`` so their own relative
imports work.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

MODULE_NAMESPACE = "nparseplus_user_plugins"
FACTORY_NAME = "create_plugin"


class PluginLoadError(Exception):
    """A plugin module could not be imported or is malformed."""


def plugin_entry_file(path: Path) -> Path:
    """Resolve a plugin path (file or package dir) to the file to import."""
    if path.is_dir():
        init = path / "__init__.py"
        if not init.is_file():
            raise PluginLoadError(f"{path} is a directory without __init__.py")
        return init
    if path.suffix == ".py" and path.is_file():
        return path
    raise PluginLoadError(f"{path} is not a .py file or a plugin package directory")


def import_plugin_module(path: Path) -> ModuleType:
    """Import the plugin at ``path`` exactly like the host app does."""
    path = Path(path)
    entry = plugin_entry_file(path)
    stem = path.stem if path.is_dir() else entry.stem
    module_name = f"{MODULE_NAMESPACE}.{stem}"
    search = [str(path)] if path.is_dir() else None
    spec = importlib.util.spec_from_file_location(
        module_name, entry, submodule_search_locations=search
    )
    if spec is None or spec.loader is None:
        raise PluginLoadError(f"could not build an import spec for {entry}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(module_name, None)
        raise
    return module


def load_plugin_factory(path: Path):
    """Import the plugin and return its ``create_plugin`` factory."""
    module = import_plugin_module(path)
    factory = getattr(module, FACTORY_NAME, None)
    if not callable(factory):
        raise PluginLoadError(
            f"{path} has no callable {FACTORY_NAME}() — every plugin module "
            "must define a module-level create_plugin() factory"
        )
    return factory
