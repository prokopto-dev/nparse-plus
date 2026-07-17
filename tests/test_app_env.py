"""_runtime_env_defaults — Linux-only env defaults, never overriding."""

from __future__ import annotations

from nparseplus.app import _runtime_env_defaults


def defaults(platform="linux", environ=None, *, frozen=False, userns_restricted=False):
    return _runtime_env_defaults(
        platform,
        environ if environ is not None else {},
        frozen=frozen,
        userns_restricted=userns_restricted,
    )


def test_linux_source_run_gets_xcb_only() -> None:
    assert defaults() == {"QT_QPA_PLATFORM": "xcb"}


def test_linux_frozen_adds_sandbox_disable() -> None:
    assert defaults(frozen=True) == {
        "QT_QPA_PLATFORM": "xcb",
        "QTWEBENGINE_DISABLE_SANDBOX": "1",
    }


def test_linux_userns_restricted_adds_sandbox_disable() -> None:
    assert defaults(userns_restricted=True)["QTWEBENGINE_DISABLE_SANDBOX"] == "1"


def test_explicit_values_never_overridden() -> None:
    environ = {"QT_QPA_PLATFORM": "offscreen", "QTWEBENGINE_DISABLE_SANDBOX": "0"}
    assert defaults(environ=environ, frozen=True, userns_restricted=True) == {}


def test_non_linux_platforms_untouched() -> None:
    assert defaults(platform="darwin", frozen=True) == {}
    assert defaults(platform="win32", frozen=True) == {}
