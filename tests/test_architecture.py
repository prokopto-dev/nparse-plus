"""Architecture guard: nparseplus.core/config/net must never import PySide6.

Runs each layer's import in a subprocess with PySide6 poisoned so any direct
or transitive Qt import fails loudly.

nparseplus_sdk is guarded too: it is the contract third-party plugins import,
and its host re-exports (events/timers/ui) are deliberately lazy so that
`import nparseplus_sdk` never drags Qt into a plugin's import graph.
"""

import pytest

_BODY = """
import importlib
import pkgutil
import {pkg}

for mod in pkgutil.walk_packages({pkg}.__path__, prefix="{pkg}."):
    importlib.import_module(mod.name)
print("ok")
"""


@pytest.mark.parametrize(
    "pkg",
    ["nparseplus.core", "nparseplus.config", "nparseplus.net", "nparseplus_sdk"],
)
def test_layer_is_qt_free(pkg: str, poisoned_import) -> None:
    try:
        __import__(pkg)
    except ModuleNotFoundError:
        pytest.skip(f"{pkg} does not exist yet")
    result = poisoned_import(["PySide6"], _BODY.replace("{pkg}", pkg))
    assert result.returncode == 0 and "ok" in result.stdout, (
        f"{pkg} (or a submodule) imports PySide6 or fails to import:\n{result.stderr}"
    )


def test_the_poison_actually_bites(poisoned_import) -> None:
    """Guard the guard.

    The previous finder implemented find_module/load_module, removed from
    CPython's import system in 3.12 — so it silently let every import through
    and the layer tests above passed vacuously for the whole 3.12 era.
    """
    result = poisoned_import(["PySide6"], "import PySide6\nprint('ok')\n")
    assert result.returncode != 0, "the Qt poison no longer blocks PySide6"
    assert "forbidden" in result.stderr
