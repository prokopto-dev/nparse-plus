"""Flatpak in-place update through the portal UpdateMonitor (#74).

The wire is asserted directly — every message the client builds is a real
jeepney ``Message`` that is round-tripped through jeepney's own parser, so a
signature typo fails here rather than inside a sandbox nobody can reach from
CI. The flow is driven over :class:`FakeConnection`, which implements the four
methods of jeepney's blocking connection that the client actually uses.

What these tests CANNOT cover, and what has to be checked on a real
Flatpak install, is listed in docs/development/releasing.md: that the portal
answers at all under the sandbox's D-Bus policy, that ``Update`` finds the
origin remote the bundle configured, and that ``Spawn`` comes back up on the
new deploy.
"""

from __future__ import annotations

import re
from collections import deque
from contextlib import contextmanager
from pathlib import Path

import jeepney
import pytest
from jeepney.low_level import HeaderFields

from nparseplus import flatpakportal, updater
from nparseplus.flatpakportal import (
    PortalOutcome,
    PortalProgress,
    PortalStatus,
    close_monitor_message,
    create_update_monitor_message,
    install_update,
    parse_progress,
    portal_supported,
    progress_match_rule,
    relaunch,
    spawn_message,
    update_message,
    version_message,
)

MONITOR_PATH = "/org/freedesktop/portal/Flatpak/update_monitor/1_57/nparse"
MANIFEST = Path(__file__).resolve().parents[1] / "packaging/flatpak"
MANIFEST_FILE = MANIFEST / "io.github.prokopto_dev.nparse_plus.yml"


# -- fake bus ------------------------------------------------------------------------


def progress_signal(
    status: int = flatpakportal.PROGRESS_RUNNING,
    *,
    n_ops: int = 1,
    op: int = 1,
    percent: int = 0,
    error: str = "",
    error_message: str = "",
    path: str = MONITOR_PATH,
) -> jeepney.low_level.Message:
    info = {
        "n_ops": ("u", n_ops),
        "op": ("u", op),
        "progress": ("u", percent),
        "status": ("u", status),
    }
    if error:
        info["error"] = ("s", error)
    if error_message:
        info["error_message"] = ("s", error_message)
    emitter = jeepney.DBusAddress(
        path,
        bus_name=flatpakportal.PORTAL_BUS_NAME,
        interface=flatpakportal.MONITOR_INTERFACE,
    )
    return jeepney.new_signal(emitter, "Progress", "a{sv}", (info,))


class FakeConnection:
    """Stands in for ``jeepney.io.blocking.DBusConnection``.

    Four methods, which is exactly the surface the portal client uses — the
    point of keeping that surface small is that this can be a plain object
    rather than a mock of a socket.
    """

    def __init__(
        self,
        *,
        version: int = 2,
        monitor_path: str = MONITOR_PATH,
        signals: list = (),
        errors: dict[str, str] | None = None,
    ) -> None:
        self.version = version
        self.monitor_path = monitor_path
        self.signals = list(signals)
        self.errors = dict(errors or {})
        self.calls: list[str] = []
        self.sent: list = []
        self.closed = False
        self._queue: deque | None = None

    def send_and_get_reply(self, message, timeout=None):
        member = str(message.header.fields[HeaderFields.member])
        self.calls.append(member)
        self.sent.append(message)
        if member in self.errors:
            return jeepney.new_error(message, self.errors[member], "s", ("refused",))
        if member == "Get":
            return jeepney.new_method_return(message, "v", (("u", self.version),))
        if member == "CreateUpdateMonitor":
            return jeepney.new_method_return(message, "o", (self.monitor_path,))
        if member == "Update" and self._queue is not None:
            self._queue.extend(self.signals)
        if member == "Spawn":
            return jeepney.new_method_return(message, "u", (4242,))
        return jeepney.new_method_return(message)

    @contextmanager
    def filter(self, rule, *, queue=None, bufsize=1):
        self.rule = rule
        self._queue = deque(maxlen=bufsize) if queue is None else queue
        try:
            yield self._queue
        finally:
            self._queue = None

    def recv_until_filtered(self, queue, timeout=None):
        if not queue:
            raise TimeoutError("no further signals")
        return queue.popleft()

    def close(self) -> None:
        self.closed = True


def run(connection: FakeConnection, **kwargs) -> PortalOutcome:
    """``install_update`` over a fake bus, pretending we are sandboxed."""
    return install_update(connection=connection, in_flatpak=True, version="9.9.9", **kwargs)


# -- the wire ------------------------------------------------------------------------


def field(message, name) -> object:
    return message.header.fields.get(name)


def round_trip(message):
    """Serialise and re-parse — proves the declared signature fits the body."""
    parser = jeepney.Parser()
    parser.add_data(message.serialise(serial=7))
    return parser.get_next_message()


@pytest.mark.parametrize(
    ("message", "path", "interface", "member", "signature"),
    [
        (
            version_message(),
            flatpakportal.PORTAL_PATH,
            "org.freedesktop.DBus.Properties",
            "Get",
            "ss",
        ),
        (
            create_update_monitor_message(),
            flatpakportal.PORTAL_PATH,
            flatpakportal.PORTAL_INTERFACE,
            "CreateUpdateMonitor",
            "a{sv}",
        ),
        (
            update_message(MONITOR_PATH),
            MONITOR_PATH,
            flatpakportal.MONITOR_INTERFACE,
            "Update",
            "sa{sv}",
        ),
        (
            close_monitor_message(MONITOR_PATH),
            MONITOR_PATH,
            flatpakportal.MONITOR_INTERFACE,
            "Close",
            None,
        ),
        (
            spawn_message(("/app/bin/nparseplus",), "/tmp"),
            flatpakportal.PORTAL_PATH,
            flatpakportal.PORTAL_INTERFACE,
            "Spawn",
            "ayaaya{uh}a{ss}ua{sv}",
        ),
    ],
)
def test_every_message_addresses_the_portal_and_encodes(
    message, path, interface, member, signature
) -> None:
    assert field(message, HeaderFields.destination) == flatpakportal.PORTAL_BUS_NAME
    assert field(message, HeaderFields.path) == path
    assert field(message, HeaderFields.interface) == interface
    assert field(message, HeaderFields.member) == member
    assert field(message, HeaderFields.signature) == signature
    assert round_trip(message).body == message.body


def test_the_bus_name_is_the_portal_and_not_the_session_helper() -> None:
    """The premise #74 was filed on: these two are the same string.

    ``org.freedesktop.Flatpak`` is flatpak-session-helper, whose
    ``Development.HostCommand`` runs commands on the host. Talking to it is a
    sandbox escape, and nothing here may address it.
    """
    assert flatpakportal.PORTAL_BUS_NAME == flatpakportal.PORTAL_INTERFACE
    assert flatpakportal.PORTAL_BUS_NAME != "org.freedesktop.Flatpak"
    source = Path(flatpakportal.__file__).read_text(encoding="utf-8")
    for message in (
        version_message(),
        create_update_monitor_message(),
        update_message(MONITOR_PATH),
        spawn_message(("x",), "/"),
    ):
        assert field(message, HeaderFields.destination) != "org.freedesktop.Flatpak"
    assert "--talk-name" not in source


def test_the_manifest_grants_no_extra_permission_for_this() -> None:
    """No finish-args change — that is the whole scheduling story of #74.

    A permission the installed version lacks makes every in-app update fail
    with NotSupported for exactly one release hop, so this line is worth a
    test rather than a comment alone.
    """
    manifest = MANIFEST_FILE.read_text(encoding="utf-8")
    granted = re.findall(r"^\s*- (--[^\s#]+)", manifest, re.MULTILINE)
    assert "--talk-name=org.freedesktop.Flatpak" not in granted
    assert "--talk-name=org.freedesktop.portal.Flatpak" not in granted


def test_relaunch_command_is_the_one_the_manifest_installs() -> None:
    """Going straight to sys.executable would skip nparseplus.sh's env setup."""
    manifest = MANIFEST_FILE.read_text(encoding="utf-8")
    assert f"install -Dm755 nparseplus.sh {flatpakportal.RELAUNCH_COMMAND}" in manifest
    assert f"app-id: {flatpakportal.FLATPAK_APP_ID}" in manifest


def test_spawn_asks_for_the_latest_deployed_version() -> None:
    message = spawn_message(("/app/bin/nparseplus", "--flag"), "/app/opt")
    cwd, argv, fds, envs, flags, options = message.body
    # GVariant bytestrings are NUL-terminated; a bare encode() is not one.
    assert cwd == b"/app/opt\0"
    assert argv == [b"/app/bin/nparseplus\0", b"--flag\0"]
    assert flags == flatpakportal.SPAWN_FLAG_LATEST_VERSION == 2
    assert fds == {} and envs == {} and options == {}


def test_the_progress_rule_is_scoped_to_this_monitor() -> None:
    rule = progress_match_rule(MONITOR_PATH).serialise()
    assert f"path='{MONITOR_PATH}'" in rule
    assert f"interface='{flatpakportal.MONITOR_INTERFACE}'" in rule
    assert "member='Progress'" in rule
    assert "type='signal'" in rule


# -- decoding ------------------------------------------------------------------------


def test_parse_progress_reads_a_real_signal_body() -> None:
    report = parse_progress(
        round_trip(progress_signal(n_ops=4, op=2, percent=55)).body,
    )
    assert (report.n_ops, report.op, report.percent) == (4, 2, 55)
    assert report.running
    assert report.label() == "Step 2 of 4 — 55%"


def test_parse_progress_survives_a_signal_it_does_not_understand() -> None:
    """The portal is free to add keys; a surprise must not end the install."""
    assert parse_progress(()) == PortalProgress()
    assert parse_progress(("not a dict",)) == PortalProgress()
    junk = parse_progress(({"status": ("s", "nonsense"), "surprise": ("b", True)},))
    assert junk.status == 0 and junk.running


def test_progress_percent_is_clamped() -> None:
    assert parse_progress(({"progress": ("u", 300)},)).percent == 100


# -- the flow ------------------------------------------------------------------------


def test_a_successful_install_reports_ok_and_offers_a_restart() -> None:
    connection = FakeConnection(
        signals=[
            progress_signal(percent=10),
            progress_signal(percent=90),
            progress_signal(flatpakportal.PROGRESS_DONE, percent=100),
        ]
    )
    outcome = run(connection)
    assert outcome.status is PortalStatus.OK
    assert outcome.ok and outcome.can_relaunch and not outcome.fall_back
    assert outcome.version == "9.9.9"
    assert "9.9.9" in outcome.message() and "Restart" in outcome.message()
    assert connection.calls == ["Get", "CreateUpdateMonitor", "AddMatch", "Update", "Close"]


def test_progress_reaches_the_caller_for_every_signal() -> None:
    seen: list[PortalProgress] = []
    connection = FakeConnection(
        signals=[
            progress_signal(percent=25),
            progress_signal(percent=75),
            progress_signal(flatpakportal.PROGRESS_DONE),
        ]
    )
    assert run(connection, progress=seen.append).ok
    assert [p.percent for p in seen] == [25, 75, 0]


def test_nothing_to_pull_is_its_own_answer_not_a_failure() -> None:
    """The OSTree repo lags the GitHub release by a few minutes on release day."""
    connection = FakeConnection(signals=[progress_signal(flatpakportal.PROGRESS_EMPTY)])
    outcome = run(connection)
    assert outcome.status is PortalStatus.ALREADY_CURRENT
    assert not outcome.fall_back  # the portal answered; say so
    assert "nothing newer" in outcome.message()


def test_a_permission_widening_update_names_flatpak_update() -> None:
    connection = FakeConnection(errors={"Update": flatpakportal.ERROR_NOT_SUPPORTED})
    outcome = run(connection)
    assert outcome.status is PortalStatus.NOT_SUPPORTED
    assert not outcome.fall_back and not outcome.can_relaunch
    assert f"flatpak update {flatpakportal.FLATPAK_APP_ID}" in outcome.message()


def test_not_supported_is_recognised_when_it_arrives_as_progress() -> None:
    """The portal does the work in a thread, so the refusal can land here."""
    connection = FakeConnection(
        signals=[
            progress_signal(
                flatpakportal.PROGRESS_FAILED,
                error=flatpakportal.ERROR_NOT_SUPPORTED,
                error_message="permissions changed",
            )
        ]
    )
    outcome = run(connection)
    assert outcome.status is PortalStatus.NOT_SUPPORTED
    assert "permissions changed" in outcome.detail


def test_a_failed_transaction_keeps_the_portals_own_words() -> None:
    connection = FakeConnection(
        signals=[
            progress_signal(
                flatpakportal.PROGRESS_FAILED,
                error="org.freedesktop.Flatpak.Error",
                error_message="Server returned 502",
            )
        ]
    )
    outcome = run(connection)
    assert outcome.status is PortalStatus.FAILED
    assert not outcome.fall_back
    assert "Server returned 502" in outcome.detail
    assert "untouched" in outcome.message()


@pytest.mark.parametrize(
    "error",
    [
        "org.freedesktop.DBus.Error.ServiceUnknown",
        "org.freedesktop.DBus.Error.AccessDenied",
        "org.freedesktop.DBus.Error.UnknownMethod",
    ],
)
def test_a_portal_that_is_absent_or_refuses_falls_back(error: str) -> None:
    """ "Not reached" must look nothing like "tried and failed" (#74's degrade rule)."""
    outcome = run(FakeConnection(errors={"Get": error}))
    assert outcome.status is PortalStatus.UNAVAILABLE
    assert outcome.fall_back


def test_a_portal_too_old_for_updatemonitor_never_asks_for_one() -> None:
    connection = FakeConnection(version=1)
    outcome = run(connection)
    assert outcome.status is PortalStatus.UNAVAILABLE and outcome.fall_back
    assert connection.calls == ["Get"]
    assert "version 1" in outcome.detail


def test_no_session_bus_falls_back(monkeypatch) -> None:
    def boom() -> None:
        raise OSError("no DBUS_SESSION_BUS_ADDRESS")

    monkeypatch.setattr(flatpakportal, "_open_connection", boom)
    outcome = install_update(in_flatpak=True)
    assert outcome.status is PortalStatus.UNAVAILABLE and outcome.fall_back


def test_outside_a_sandbox_nothing_touches_dbus() -> None:
    connection = FakeConnection()
    outcome = install_update(connection=connection, in_flatpak=False)
    assert outcome.status is PortalStatus.UNAVAILABLE and outcome.fall_back
    assert connection.calls == []


def test_without_jeepney_the_path_is_simply_not_offered(monkeypatch) -> None:
    """macOS and Windows builds carry no D-Bus client — see pyproject's marker."""
    monkeypatch.setattr(flatpakportal, "jeepney", None)
    assert not portal_supported(in_flatpak=True)
    connection = FakeConnection()
    outcome = install_update(connection=connection, in_flatpak=True)
    assert outcome.status is PortalStatus.UNAVAILABLE and outcome.fall_back
    assert connection.calls == []


def test_the_sandbox_probe_is_the_one_the_updater_already_uses(monkeypatch) -> None:
    """#74 must not grow a second way to ask 'am I in a Flatpak?'.

    The updater's ``/.flatpak-info`` probe already decides ``.flatpak`` vs
    ``.tar.gz``; a second one could disagree with it, and then the app would
    offer an in-place update for a tarball install.
    """
    assert flatpakportal.running_in_flatpak is updater.running_in_flatpak
    asked: list[bool] = []
    monkeypatch.setattr(flatpakportal, "running_in_flatpak", lambda: asked.append(True) or False)
    assert portal_supported() is False
    assert asked == [True]


def test_the_portal_going_quiet_is_a_failure_not_a_hang() -> None:
    outcome = run(FakeConnection(signals=[progress_signal(percent=5)]))
    assert outcome.status is PortalStatus.FAILED
    assert "progress" in outcome.detail


def test_the_monitor_is_closed_even_when_the_update_fails() -> None:
    connection = FakeConnection(errors={"Update": "org.freedesktop.Flatpak.Error"})
    assert run(connection).status is PortalStatus.FAILED
    assert connection.calls[-1] == "Close"


def test_an_injected_connection_is_left_open() -> None:
    """Production opens and closes its own; a caller's is the caller's."""
    connection = FakeConnection(signals=[progress_signal(flatpakportal.PROGRESS_DONE)])
    assert run(connection).ok
    assert not connection.closed


def test_a_connection_we_opened_is_closed(monkeypatch) -> None:
    connection = FakeConnection(signals=[progress_signal(flatpakportal.PROGRESS_DONE)])
    monkeypatch.setattr(flatpakportal, "_open_connection", lambda: connection)
    assert install_update(in_flatpak=True).ok
    assert connection.closed


def test_an_advisory_addmatch_refusal_does_not_stop_the_install() -> None:
    """The portal signals us directly; AddMatch is insurance, not a step."""
    connection = FakeConnection(
        errors={"AddMatch": "org.freedesktop.DBus.Error.AccessDenied"},
        signals=[progress_signal(flatpakportal.PROGRESS_DONE)],
    )
    assert run(connection).ok


# -- relaunch ------------------------------------------------------------------------


def test_relaunch_spawns_the_latest_version() -> None:
    connection = FakeConnection()
    assert relaunch(connection=connection, in_flatpak=True, cwd="/app/opt") is True
    (spawn,) = [m for m in connection.sent if field(m, HeaderFields.member) == "Spawn"]
    assert spawn.body[1] == [flatpakportal.RELAUNCH_COMMAND.encode() + b"\0"]
    assert spawn.body[4] == flatpakportal.SPAWN_FLAG_LATEST_VERSION


def test_relaunch_says_no_rather_than_raising_when_the_portal_refuses() -> None:
    connection = FakeConnection(errors={"Spawn": "org.freedesktop.DBus.Error.AccessDenied"})
    assert relaunch(connection=connection, in_flatpak=True, cwd="/") is False


def test_relaunch_is_not_offered_outside_a_sandbox() -> None:
    connection = FakeConnection()
    assert relaunch(connection=connection, in_flatpak=False) is False
    assert connection.calls == []


# -- prose ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", list(PortalStatus))
def test_every_status_has_something_to_say(status: PortalStatus) -> None:
    outcome = PortalOutcome(status=status, version="1.2.3")
    assert outcome.title() and outcome.message()
    assert outcome.fall_back is (status is PortalStatus.UNAVAILABLE)
    assert outcome.can_relaunch is (status is PortalStatus.OK)


def test_the_labels_a_progress_bar_shows() -> None:
    assert PortalProgress(n_ops=1, op=1, percent=40).label() == "40%"
    assert PortalProgress(status=flatpakportal.PROGRESS_DONE).label() == "Installed."
    assert PortalProgress(status=flatpakportal.PROGRESS_EMPTY).label() == "Nothing to install."
    assert (
        PortalProgress(status=flatpakportal.PROGRESS_FAILED, error_message="disk full").label()
        == "disk full"
    )
