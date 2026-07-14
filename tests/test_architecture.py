"""Architecture guard: nparseplus.core/config/net must never import PySide6.

Runs each layer's import in a subprocess with PySide6 poisoned so any direct
or transitive Qt import fails loudly.
"""

import subprocess
import sys

import pytest

_POISON_PRELUDE = """
import importlib, sys

class _Poison:
    def find_module(self, name, path=None):
        if name == "PySide6" or name.startswith("PySide6."):
            return self
    def load_module(self, name):
        raise ImportError(f"Qt import forbidden in this layer: {name}")

sys.meta_path.insert(0, _Poison())
import pkgutil
import {pkg}

for mod in pkgutil.walk_packages({pkg}.__path__, prefix="{pkg}."):
    importlib.import_module(mod.name)
print("ok")
"""


@pytest.mark.parametrize("pkg", ["nparseplus.core", "nparseplus.config", "nparseplus.net"])
def test_layer_is_qt_free(pkg: str) -> None:
    try:
        __import__(pkg)
    except ModuleNotFoundError:
        pytest.skip(f"{pkg} does not exist yet")
    result = subprocess.run(
        [sys.executable, "-c", _POISON_PRELUDE.replace("{pkg}", pkg)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0 and "ok" in result.stdout, (
        f"{pkg} (or a submodule) imports PySide6 or fails to import:\n{result.stderr}"
    )
