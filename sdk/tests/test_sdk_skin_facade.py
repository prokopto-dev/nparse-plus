"""``nparseplus_sdk.skin`` — the lazy skin re-export (SDK 1.4).

The façade exists so that "match the app's design language" does not mean
either hardcoded hex (stale the moment the user switches skin, which is live)
or ``import nparseplus.ui.skins`` (host internals, no compat promise, the part
of the codebase most likely to churn). What is asserted here is the shape of
the promise: an explicit allowlist, resolving to the host, importable without
Qt, and describing every skin a user can pick.
"""

from __future__ import annotations

import pytest

from nparseplus_sdk import skin


def test_exports_are_an_explicit_allowlist():
    """A curated façade, not a re-export of ``ui/skins.py``. Everything named
    here is frozen for the whole 1.x line, so the list is the decision."""
    assert set(skin.EXPORTS) == {
        "AppSkin",
        "current",
        "SKIN_NAMES",
        "TypographyRole",
        "SMALL_DISPLAY",
        "BODY_TEXT",
        "NUMERIC_TEXT",
        "px",
        "tracking",
        "typography_style",
        "TITLE",
        "ROW_NAME",
        "ROW_VALUE",
        "GOOD",
        "BAD",
        "COOLDOWN",
        "TIMER",
        "ROLL",
        "POP_WINDOW",
        "LINK",
        "shade",
        "rgba",
        "gradient",
    }


def test_every_exported_name_resolves_to_the_host_facade():
    from nparseplus.ui import pluginskin

    for name in skin.EXPORTS:
        assert getattr(skin, name) is getattr(pluginskin, name)


def test_the_hosts_internal_skin_layer_is_not_reachable_through_it():
    """The whole point of curating: ``Skin`` itself, its ~50 tokens and the
    builders around them stay internal and free to move."""
    for name in ("Skin", "SKINS", "DUXA", "set_skin", "overlay_window_style"):
        with pytest.raises(AttributeError):
            getattr(skin, name)


def test_dir_lists_the_allowlist():
    assert set(dir(skin)) == set(skin.EXPORTS) | {"EXPORTS"}


def test_importing_skin_does_not_pull_qt():
    """``ui/skins.py``, ``ui/theme.py`` and ``ui/chrome.py`` are Qt-free data
    plus pure stylesheet builders, which is what makes a curated façade over
    them cheap: a plugin can build its stylesheet in a unit test with no
    QApplication, and importing the SDK never drags Qt into its graph."""
    import subprocess
    import sys

    code = (
        "import sys; sys.modules['PySide6'] = None;\n"
        "from nparseplus_sdk import skin;\n"
        "app = skin.current();\n"
        "assert app.overlay_stylesheet();\n"
        "assert 'PySide6.QtWidgets' not in sys.modules;\n"
        "print('ok')"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout


def test_a_snapshot_describes_every_skin_a_user_can_pick():
    from nparseplus.ui import skins as host_skins

    for name in skin.SKIN_NAMES:
        host_skins.set_skin(name)
        app = skin.current()
        assert app.name == name
        assert app.text and app.surface and app.accent
        assert app.base_font_size >= 6
    host_skins.set_skin(host_skins.DEFAULT_SKIN)


def test_the_worked_example_from_the_docstring_builds_a_sheet():
    app = skin.current()

    style = app.typography(skin.NUMERIC_TEXT, color=app.heading)
    assert f"color: {app.heading}" in style
    assert app.bar_stylesheet(skin.GOOD).startswith("QProgressBar")
    assert f"#{skin.ROW_VALUE}" in app.overlay_stylesheet()


def test_unknown_names_raise_attribute_error():
    with pytest.raises(AttributeError, match="has no attribute 'plate'"):
        skin.plate  # noqa: B018 - the access is the assertion
