"""Which route "Install" takes, and how the Flatpak one degrades (#74).

The real methods are driven over a ``SimpleNamespace`` stub — constructing
``NomnsParse`` would build every legacy window (same trick as
tests/ui/test_tray_plugins_entry.py).
"""

from __future__ import annotations

import inspect
from types import SimpleNamespace
from typing import ClassVar

import pytest

from nparseplus import flatpakportal
from nparseplus.flatpakportal import PortalOutcome, PortalStatus
from nparseplus.helpers import application
from nparseplus.helpers.application import NomnsParse
from nparseplus.updater import ReleaseInfo

pytestmark = pytest.mark.qt


def _release() -> ReleaseInfo:
    return ReleaseInfo(version="9.9.9", html_url="https://example/releases/9.9.9")


class _RecordingDialog:
    """Stands in for PortalOutcomeDialog: records, never opens a window."""

    made: ClassVar[list] = []

    def __init__(self, outcome, parent=None) -> None:
        self.outcome = outcome
        self.restart_requested = SimpleNamespace(connect=lambda _slot: None)
        self.open_release_requested = SimpleNamespace(connect=lambda _slot: None)
        _RecordingDialog.made.append(outcome)

    def exec(self) -> int:
        return 0


@pytest.fixture
def routes(monkeypatch):
    """A stub app whose two install routes just record which one was taken."""
    _RecordingDialog.made = []
    monkeypatch.setattr(application, "PortalOutcomeDialog", _RecordingDialog)
    taken: list[str] = []
    return SimpleNamespace(
        taken=taken,
        dialogs=_RecordingDialog.made,
        app=SimpleNamespace(
            _available_release=_release(),
            _portal_window=None,
            _start_portal_update=lambda release: taken.append("portal"),
            _start_download_install=lambda release: taken.append("download"),
            _restart_into_new_version=lambda: taken.append("restart"),
        ),
    )


def test_a_flatpak_install_goes_through_the_portal(routes, monkeypatch) -> None:
    monkeypatch.setattr(flatpakportal, "portal_supported", lambda: True)
    NomnsParse._install_available_update(routes.app)
    assert routes.taken == ["portal"]


def test_every_other_install_still_downloads(routes, monkeypatch) -> None:
    monkeypatch.setattr(flatpakportal, "portal_supported", lambda: False)
    NomnsParse._install_available_update(routes.app)
    assert routes.taken == ["download"]


def test_an_unreachable_portal_falls_back_without_saying_anything(routes) -> None:
    """UNAVAILABLE means we never got far enough to have told the user
    anything — so the app does what it did before #74, silently."""
    NomnsParse._on_portal_finished(
        routes.app, PortalOutcome(status=PortalStatus.UNAVAILABLE, detail="no session bus")
    )
    assert routes.taken == ["download"]
    assert routes.dialogs == []


@pytest.mark.parametrize(
    "status",
    [
        PortalStatus.OK,
        PortalStatus.ALREADY_CURRENT,
        PortalStatus.NOT_SUPPORTED,
        PortalStatus.FAILED,
    ],
)
def test_a_portal_that_answered_is_reported_not_retried(routes, status) -> None:
    """The user is owed the portal's answer, not a second slower attempt."""
    NomnsParse._on_portal_finished(routes.app, PortalOutcome(status=status))
    assert routes.taken == []
    assert [outcome.status for outcome in routes.dialogs] == [status]


def test_the_progress_window_is_torn_down_when_the_install_ends(routes) -> None:
    closed: list[str] = []
    routes.app._portal_window = SimpleNamespace(
        close=lambda: closed.append("close"),
        deleteLater=lambda: closed.append("delete"),
    )
    NomnsParse._on_portal_finished(routes.app, PortalOutcome(status=PortalStatus.OK))
    assert closed == ["close", "delete"]
    assert routes.app._portal_window is None


def test_a_refused_relaunch_does_not_quit_into_nothing(monkeypatch) -> None:
    """The update is installed either way; quitting with no way back is worse
    than telling the user to restart it themselves."""
    monkeypatch.setattr(flatpakportal, "relaunch", lambda: False)
    said: list[str] = []
    monkeypatch.setattr(
        application.QMessageBox,
        "information",
        staticmethod(lambda *args, **kwargs: said.append(args[2])),
    )
    quit_calls: list[str] = []
    app = SimpleNamespace(_quit_app=lambda: quit_calls.append("quit"))
    NomnsParse._restart_into_new_version(app)
    assert quit_calls == []
    assert said and "installed" in said[0]


def test_a_successful_relaunch_quits_through_the_tray_quit_path(monkeypatch) -> None:
    monkeypatch.setattr(flatpakportal, "relaunch", lambda: True)
    quit_calls: list[str] = []
    app = SimpleNamespace(_quit_app=lambda: quit_calls.append("quit"))
    NomnsParse._restart_into_new_version(app)
    assert quit_calls == ["quit"]
    # The tray's Quit arm and the restart must not drift apart: both persist
    # window state and clear the tray icon before quitting.
    assert "self._quit_app()" in inspect.getsource(NomnsParse._menu)


def test_the_dialog_is_told_which_route_the_button_takes(qtbot, monkeypatch) -> None:
    monkeypatch.setattr(flatpakportal, "portal_supported", lambda: True)
    app = SimpleNamespace(
        _available_release=_release(),
        _update_window=None,
        _install_available_update=lambda: None,
        _clear_update_window=lambda _result=None: None,
    )
    NomnsParse._show_update_window(app)
    qtbot.addWidget(app._update_window)
    assert app._update_window.install_button.text() == "Install Update"
