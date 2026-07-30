"""The add-on subsystem is opt-in, and "off" has to mean genuinely absent.

A user who only wants maps and spell timers should not pay for plugins in
startup cost, in imports, or in UI surface — and should not have to know the
feature exists. These tests pin that property structurally, because it is the
kind of thing a well-meaning refactor breaks silently: hoisting one import to
module scope in app.py costs nothing visible and quietly undoes it.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from nparseplus.config.settings import NO_PLUGINS_ENV_VAR, Settings, plugins_enabled

SRC = Path(__file__).resolve().parents[3] / "src" / "nparseplus"

# Importing any of these means the plugin subsystem woke up.
PLUGIN_NAMESPACES = (
    "nparseplus.pluginbootstrap",
    "nparseplus.core.plugins",
    "nparseplus.ui.pluginmanager",
    "nparseplus.ui.pluginconsent",
    "nparseplus.ui.pluginwindow",
    "nparseplus_sdk",
)


def test_plugins_are_off_by_default() -> None:
    assert Settings().plugins.enabled is False


@pytest.mark.parametrize(
    ("enabled", "env", "expected"),
    [
        (False, {}, False),
        (True, {}, True),
        # The env var is a veto...
        (True, {NO_PLUGINS_ENV_VAR: "1"}, False),
        # ...and never an enabler: safe mode must not be able to switch on a
        # subsystem the user never opted into.
        (False, {NO_PLUGINS_ENV_VAR: "0"}, False),
        (False, {NO_PLUGINS_ENV_VAR: "1"}, False),
        (True, {NO_PLUGINS_ENV_VAR: "0"}, True),
    ],
)
def test_env_var_vetoes_but_never_enables(enabled: bool, env: dict, expected: bool) -> None:
    settings = Settings()
    settings.plugins.enabled = enabled
    assert plugins_enabled(settings, env) is expected


def test_importing_the_app_pulls_in_no_plugin_modules(poisoned_import) -> None:
    """`import nparseplus.app` must not reach the plugin subsystem."""
    result = poisoned_import(list(PLUGIN_NAMESPACES), "import nparseplus.app\nprint('ok')\n")
    assert result.returncode == 0 and "ok" in result.stdout, (
        f"nparseplus.app imports a plugin module at module scope:\n{result.stderr}"
    )


def test_importing_the_settings_window_pulls_in_no_plugin_modules(poisoned_import) -> None:
    """`nparseplus.ui.settingswindow` must not reach the plugin subsystem.

    The test above does not cover this: `create_app` imports the settings
    window function-locally, so poisoning `import nparseplus.app` never
    reaches it, and the AST test below only parses app.py. But create_app
    builds this window on every launch, plugins on or off — so a typed import
    of PluginUi to describe the Settings > Windows plugin rows would wake the
    whole subsystem for users who opted out. Hence the plain-tuple kwarg.
    """
    result = poisoned_import(
        list(PLUGIN_NAMESPACES), "import nparseplus.ui.settingswindow\nprint('ok')\n"
    )
    assert result.returncode == 0 and "ok" in result.stdout, (
        f"nparseplus.ui.settingswindow imports a plugin module at module scope:\n{result.stderr}"
    )


def _module_imports(tree: ast.AST) -> list[tuple[ast.stmt, str]]:
    """Every import statement in `tree` paired with the module it names."""
    found: list[tuple[ast.stmt, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found += [(node, alias.name) for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.append((node, node.module))
    return found


def _parents(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    return {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}


# The conditions that legitimately guard a plugin import.
GATES = ("plugins_enabled(", "plugin_host is not None", "self._plugins_enabled")


@pytest.mark.parametrize("relpath", ["app.py", "helpers/application.py"])
def test_plugin_imports_are_gated(relpath: str) -> None:
    """Plugin imports in the startup path must sit behind a runtime gate.

    The poison test above proves nothing is imported at module scope; this one
    proves the *function-local* imports are conditional too, which is what
    keeps a plugins-off launch from touching them.
    """
    source = (SRC / relpath).read_text()
    tree = ast.parse(source)
    parents = _parents(tree)

    offenders = []
    for node, module in _module_imports(tree):
        if not module.startswith(PLUGIN_NAMESPACES):
            continue
        gated = False
        walker: ast.AST | None = node
        while walker is not None:
            if isinstance(walker, ast.If) and any(g in ast.unparse(walker.test) for g in GATES):
                gated = True
                break
            # TYPE_CHECKING blocks never execute, so they are fine too.
            if isinstance(walker, ast.If) and "TYPE_CHECKING" in ast.unparse(walker.test):
                gated = True
                break
            walker = parents.get(walker)
        if not gated:
            offenders.append(f"{relpath}:{node.lineno} imports {module}")

    assert not offenders, (
        "plugin imports must be guarded so a plugins-off launch never touches "
        "them:\n  " + "\n  ".join(offenders)
    )
