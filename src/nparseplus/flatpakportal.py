"""Flatpak in-place update through the desktop portal's UpdateMonitor (#74).

A Flatpak user pressing "Download Update" got handed a ~200 MB ``.flatpak``
bundle for their software installer, when ``flatpak update`` was already the
better answer — incremental (ostree fetches only the objects the client
lacks), transactional, and GPG-verified against the remote the bundle
configured when it was installed. This module lets the app just do it.

Everything here is standard portal API and needs **no new sandbox
permission**. ``org.freedesktop.portal.Flatpak`` is both the bus name and the
interface name, and ``common/flatpak-run-dbus.c`` already grants every
sandboxed app ``--call=org.freedesktop.portal.*=*`` plus broadcasts under
``/org/freedesktop/portal/*``, which is where the ``UpdateMonitor`` object
lives. ``org.freedesktop.Flatpak`` — one component shorter — is
``flatpak-session-helper``, whose ``Development.HostCommand`` is arbitrary
command execution on the host; granting *that* would be a sandbox escape, and
``packaging/flatpak/…yml`` carries a comment saying so. Do not add either.

The flow is four calls:

1. ``CreateUpdateMonitor`` → a per-sender object path.
2. ``UpdateMonitor.Update(parent_window, options)`` → starts the install.
3. ``UpdateMonitor.Progress`` signals until ``status`` leaves *running*.
4. ``Spawn`` with ``FLATPAK_SPAWN_FLAGS_LATEST_VERSION`` → relaunch, then exit.

**We deliberately do not wait for the ``UpdateAvailable`` signal.** The portal
polls its remotes on its own schedule, and this app already knows a new
version exists — ``updater.check_for_update`` told it, off GitHub's release
API. Gating on the portal's poll would make "Install" do nothing for an
arbitrary interval; asking it to update outright answers in seconds, and the
"nothing to pull" case comes back as a ``Progress`` status of its own
(:data:`PROGRESS_EMPTY`), which is a better thing to say than silence.

Qt-free, like :mod:`nparseplus.updater` beside it: :meth:`PortalOutcome.title`
and :meth:`PortalOutcome.message` carry the prose so the dialog stays a
renderer and the wording is testable without a window. :func:`install_update`
blocks for as long as the install takes (minutes, for a big delta) — call it
from a worker thread and marshal the result yourself.

Every failure degrades. Anything that means "the portal was never reached" —
not in a sandbox, jeepney missing, no session bus, no portal, a portal too
old, a refused call — answers :data:`PortalStatus.UNAVAILABLE`, whose
:attr:`~PortalOutcome.fall_back` tells the caller to do exactly what the
updater did before this module existed. Nothing here ever raises at the
caller.
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict

from nparseplus.updater import running_in_flatpak

if TYPE_CHECKING:  # pragma: no cover - typing only
    from jeepney.low_level import Message

try:  # jeepney ships on Linux only (see pyproject) — everywhere else this
    # module still imports, and every entry point answers UNAVAILABLE.
    import jeepney
    from jeepney.bus_messages import MatchRule, message_bus
except ImportError:  # pragma: no cover - exercised by monkeypatching the flag
    jeepney = None  # type: ignore[assignment]
    MatchRule = None  # type: ignore[assignment]
    message_bus = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

#: Must match ``app-id`` in packaging/flatpak/io.github.prokopto_dev.nparse_plus.yml.
FLATPAK_APP_ID = "io.github.prokopto_dev.nparse_plus"

PORTAL_BUS_NAME = "org.freedesktop.portal.Flatpak"
#: The bus name and the interface name are the same string here. The issue
#: this module closes was filed believing they differed; they do not.
PORTAL_INTERFACE = "org.freedesktop.portal.Flatpak"
PORTAL_PATH = "/org/freedesktop/portal/Flatpak"
MONITOR_INTERFACE = "org.freedesktop.portal.Flatpak.UpdateMonitor"

#: ``CreateUpdateMonitor`` arrived in version 2 of the Flatpak portal
#: (flatpak 1.5.0). Version 1 has ``Spawn`` and nothing else we need.
MIN_PORTAL_VERSION = 2

#: ``FLATPAK_SPAWN_FLAGS_LATEST_VERSION`` — start the app from the newest
#: deployed version rather than the one this process is running from.
SPAWN_FLAG_LATEST_VERSION = 2

#: What the manifest installs as the sandbox entry point. Not ``sys.executable``:
#: that is the PyInstaller binary under /app/opt, and going straight to it
#: skips packaging/flatpak/nparseplus.sh, which is what sets QT_QPA_PLATFORM=xcb
#: (the overlays need X11 semantics) and QTWEBENGINE_DISABLE_SANDBOX. A test
#: keeps this in step with the manifest's install line.
RELAUNCH_COMMAND = "/app/bin/nparseplus"

# UpdateMonitor.Progress `status` values, from the portal's documentation.
PROGRESS_RUNNING = 0
PROGRESS_EMPTY = 1  # nothing to do — the remote had no newer commit
PROGRESS_DONE = 2
PROGRESS_FAILED = 3

ERROR_NOT_SUPPORTED = "org.freedesktop.DBus.Error.NotSupported"

#: D-Bus errors that mean "there is no portal here to talk to", as opposed to
#: "the portal answered and said no". All of them fall back to the download.
_ABSENT_PORTAL_ERRORS = frozenset(
    {
        "org.freedesktop.DBus.Error.ServiceUnknown",
        "org.freedesktop.DBus.Error.NameHasNoOwner",
        "org.freedesktop.DBus.Error.UnknownMethod",
        "org.freedesktop.DBus.Error.UnknownInterface",
        "org.freedesktop.DBus.Error.UnknownObject",
        "org.freedesktop.DBus.Error.UnknownProperty",
        # A denied call is a policy decision about this sandbox, not a defect:
        # the honest response is the route that has always worked.
        "org.freedesktop.DBus.Error.AccessDenied",
        "org.freedesktop.DBus.Error.Disconnected",
    }
)

#: Overall ceiling on one install. A cold 200 MB pull over a slow line is
#: minutes; anything past this is a portal that has stopped answering.
DEFAULT_TIMEOUT_S = 3600.0
#: How long to wait for the *next* Progress signal before giving up. The
#: portal reports continuously during a pull, so silence this long means the
#: install is not happening, whatever the overall budget still allows.
DEFAULT_IDLE_TIMEOUT_S = 300.0
#: Method calls that answer immediately (CreateUpdateMonitor, Update, Spawn).
CALL_TIMEOUT_S = 30.0


class PortalStatus(StrEnum):
    """What became of one in-place update attempt.

    A vocabulary rather than a bool, for the same reason
    :class:`nparseplus.updater.DownloadStatus` is one: the reasons are not
    interchangeable. ``UNAVAILABLE`` is the only one the user never hears
    about — it means we never got far enough to have said anything, so the
    caller quietly does what it did before.
    """

    OK = "ok"  # installed; the new version is live once the app restarts
    ALREADY_CURRENT = "already_current"  # the remote had nothing newer to pull
    UNAVAILABLE = "unavailable"  # no portal to ask — fall back to the download
    NOT_SUPPORTED = "not_supported"  # the update widens permissions (see below)
    FAILED = "failed"  # the portal tried and could not finish


class PortalProgress(BaseModel):
    """One ``UpdateMonitor.Progress`` signal.

    ``op``/``n_ops`` count the transaction's operations and ``percent`` is the
    current one's own progress. They are reported separately rather than
    folded into a single overall fraction on purpose: the portal's docs do not
    pin down whether ``op`` is 0- or 1-based, and a bar that is silently one
    step out is worse than two honest numbers.
    """

    model_config = ConfigDict(frozen=True)

    n_ops: int = 0
    op: int = 0
    percent: int = 0
    status: int = PROGRESS_RUNNING
    error: str = ""
    error_message: str = ""

    @property
    def running(self) -> bool:
        return self.status == PROGRESS_RUNNING

    def label(self) -> str:
        """The line to put under a progress bar."""
        if self.status == PROGRESS_EMPTY:
            return "Nothing to install."
        if self.status == PROGRESS_DONE:
            return "Installed."
        if self.status == PROGRESS_FAILED:
            return self.error_message or "The update failed."
        if self.n_ops > 1:
            return f"Step {self.op} of {self.n_ops} — {self.percent}%"
        return f"{self.percent}%"


ProgressCallback = Callable[[PortalProgress], None]


class PortalOutcome(BaseModel):
    """The result of an in-place update attempt — and the prose for it.

    Kept Qt-free and beside the mechanism, like
    :class:`nparseplus.updater.DownloadOutcome`, so the dialog is a pure
    renderer and every word is testable without a window.
    """

    model_config = ConfigDict(frozen=True)

    status: PortalStatus
    #: The version the update was reaching for, when the caller knew it.
    version: str = ""
    #: The technical line — a D-Bus error name, the portal's own message.
    #: What a bug report quotes, and what nobody else needs to read.
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status is PortalStatus.OK

    @property
    def can_relaunch(self) -> bool:
        """True when a new version is deployed and only a restart is missing."""
        return self.status is PortalStatus.OK

    @property
    def fall_back(self) -> bool:
        """True when the caller should download the artifact instead.

        Only ``UNAVAILABLE``. Every other status means the portal answered,
        so the user is owed that answer rather than a second, slower attempt
        at the same thing behind their back.
        """
        return self.status is PortalStatus.UNAVAILABLE

    def title(self) -> str:
        if self.status is PortalStatus.OK:
            return "Update installed"
        if self.status is PortalStatus.ALREADY_CURRENT:
            return "Nothing to install yet"
        if self.status is PortalStatus.NOT_SUPPORTED:
            return "This update needs the Flatpak tools"
        if self.status is PortalStatus.FAILED:
            return "Update failed"
        return "In-app update unavailable"

    def message(self) -> str:
        version = f"nParse+ {self.version}" if self.version else "The update"
        manual = f"flatpak update {FLATPAK_APP_ID}"
        if self.status is PortalStatus.OK:
            return (
                f"{version} has been installed. Restart nParse+ to run it — "
                "the version you are using now stays live until you do."
            )
        if self.status is PortalStatus.ALREADY_CURRENT:
            return (
                "The Flatpak repository has nothing newer to install yet. A release "
                "reaches the update repository a little after it appears on GitHub, "
                "so this usually means trying again shortly will work. You can also "
                "download the bundle from the release page."
            )
        if self.status is PortalStatus.NOT_SUPPORTED:
            return (
                f"{version} asks for a sandbox permission the installed version does "
                "not have, and Flatpak only allows an in-app update when the new "
                "version asks for the same permissions or fewer.\n\n"
                f"Install it from a terminal instead:\n\n    {manual}\n\n"
                "Your desktop's software manager can do the same thing."
            )
        if self.status is PortalStatus.FAILED:
            return (
                f"{version} could not be installed. Nothing was changed — the version "
                "you are running is untouched.\n\n"
                f"Try again, or install it from a terminal:\n\n    {manual}"
            )
        return (
            "This copy of nParse+ cannot install its own updates: the Flatpak "
            "portal is not available here."
        )


def portal_supported(in_flatpak: bool | None = None) -> bool:
    """Whether the in-place path is even worth trying — cheap, no D-Bus.

    Deliberately reuses :func:`nparseplus.updater.running_in_flatpak` (the
    ``/.flatpak-info`` probe that already decides ``.flatpak`` vs ``.tar.gz``)
    rather than inventing a second way to ask the same question. Says nothing
    about whether the portal will *answer* — that costs a round trip, and
    :func:`install_update` finds out.
    """
    if jeepney is None:
        return False
    return running_in_flatpak() if in_flatpak is None else in_flatpak


# -- message builders ---------------------------------------------------------------
#
# Pure: each returns a jeepney Message and touches no socket, so the wire
# shape is asserted directly in the tests rather than inferred from a mock.


def _portal_address() -> Any:
    return jeepney.DBusAddress(PORTAL_PATH, bus_name=PORTAL_BUS_NAME, interface=PORTAL_INTERFACE)


def _monitor_address(monitor_path: str) -> Any:
    return jeepney.DBusAddress(monitor_path, bus_name=PORTAL_BUS_NAME, interface=MONITOR_INTERFACE)


def version_message() -> Message:
    """``org.freedesktop.DBus.Properties.Get`` for the portal's ``version``."""
    return jeepney.Properties(_portal_address()).get("version")


def create_update_monitor_message() -> Message:
    """``CreateUpdateMonitor(a{sv} options) -> o handle``."""
    return jeepney.new_method_call(_portal_address(), "CreateUpdateMonitor", "a{sv}", ({},))


def update_message(monitor_path: str, parent_window: str = "") -> Message:
    """``UpdateMonitor.Update(s parent_window, a{sv} options)``.

    ``parent_window`` is the caller's window identifier, used to parent the
    portal's own confirmation dialog. Empty is legal and means "no parent" —
    the app has no main window to name, and the overlays are the wrong thing
    to hang a modal off.
    """
    return jeepney.new_method_call(
        _monitor_address(monitor_path), "Update", "sa{sv}", (parent_window, {})
    )


def close_monitor_message(monitor_path: str) -> Message:
    """``UpdateMonitor.Close()`` — the portal keeps the object until we say."""
    return jeepney.new_method_call(_monitor_address(monitor_path), "Close")


def spawn_message(argv: tuple[str, ...], cwd: str) -> Message:
    """``Spawn(ay cwd, aay argv, a{uh} fds, a{ss} envs, u flags, a{sv} options) -> u``.

    GVariant bytestrings are NUL-terminated, so every path and argument goes
    on the wire with its terminator. No file descriptors are passed: a GUI
    process needs no inherited stdio, and passing none keeps the connection
    off jeepney's ``enable_fds`` path.
    """
    return jeepney.new_method_call(
        _portal_address(),
        "Spawn",
        "ayaaya{uh}a{ss}ua{sv}",
        (
            cwd.encode() + b"\0",
            [arg.encode() + b"\0" for arg in argv],
            {},
            {},
            SPAWN_FLAG_LATEST_VERSION,
            {},
        ),
    )


# The same rule cannot serve both jobs, and using one for both silently
# discards every signal the portal sends.
#
# `AddMatch` is interpreted by the bus daemon, which tracks name ownership and
# happily resolves the well-known `org.freedesktop.portal.Flatpak` — naming it
# there is what stops the daemon forwarding some other connection's signals.
# `connection.filter()` is jeepney matching in-process, and `MatchRule.matches`
# compares the `sender` header **literally**. That header never holds a
# well-known name: the daemon rewrites it to the sending connection's unique
# name (`:1.42`), so a local rule naming the portal matches nothing, the queue
# stays empty, and the install runs to its idle timeout and reports a failure
# that never happened.
#
# So: the wire rule keeps `sender` and the local rule drops it. Nothing is
# given up by dropping it — the monitor's object path is minted per sender by
# the portal and handed to us privately, and the interface and member are
# pinned, so the remaining triple is already tighter than the sender check.


def progress_match_rule(monitor_path: str) -> Any:
    """The rule sent to the bus, naming the portal by its well-known name."""
    return MatchRule(
        type="signal",
        sender=PORTAL_BUS_NAME,
        interface=MONITOR_INTERFACE,
        member="Progress",
        path=monitor_path,
    )


def progress_filter_rule(monitor_path: str) -> Any:
    """The rule jeepney matches in-process — deliberately without ``sender``."""
    return MatchRule(
        type="signal",
        interface=MONITOR_INTERFACE,
        member="Progress",
        path=monitor_path,
    )


# -- decoding -----------------------------------------------------------------------


def _variant(value: Any) -> Any:
    """Unwrap jeepney's ``(signature, value)`` representation of a variant."""
    if isinstance(value, tuple) and len(value) == 2 and isinstance(value[0], str):
        return value[1]
    return value


def parse_progress(body: Any) -> PortalProgress:
    """Decode one ``Progress`` signal body into a :class:`PortalProgress`.

    Tolerant by construction: the portal is free to add keys, and a signal
    carrying something unexpected must not take the install down with it.
    """
    info = body[0] if body else {}
    if not isinstance(info, dict):
        info = {}

    def number(key: str) -> int:
        try:
            return int(_variant(info.get(key, 0)))
        except (TypeError, ValueError):
            return 0

    def text(key: str) -> str:
        value = _variant(info.get(key, ""))
        return value if isinstance(value, str) else ""

    return PortalProgress(
        n_ops=number("n_ops"),
        op=number("op"),
        percent=max(0, min(100, number("progress"))),
        status=number("status"),
        error=text("error"),
        error_message=text("error_message"),
    )


# -- the flow -----------------------------------------------------------------------


class _PortalError(Exception):
    """A D-Bus error reply, classified into a :class:`PortalStatus`."""

    def __init__(self, status: PortalStatus, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _classify(error_name: str, detail: str) -> _PortalError:
    if error_name == ERROR_NOT_SUPPORTED:
        return _PortalError(PortalStatus.NOT_SUPPORTED, detail)
    if error_name in _ABSENT_PORTAL_ERRORS:
        return _PortalError(PortalStatus.UNAVAILABLE, detail)
    return _PortalError(PortalStatus.FAILED, detail)


def _call(connection: Any, message: Message, *, timeout: float = CALL_TIMEOUT_S) -> Any:
    """Send a method call and return its body, classifying any error reply."""
    reply = connection.send_and_get_reply(message, timeout=timeout)
    if reply.header.message_type is jeepney.MessageType.error:
        name = str(reply.header.fields.get(jeepney.HeaderFields.error_name, ""))
        text = reply.body[0] if reply.body and isinstance(reply.body[0], str) else ""
        raise _classify(name, f"{name}: {text}" if text else name)
    return reply.body


def _open_connection() -> Any:
    from jeepney.io.blocking import open_dbus_connection

    return open_dbus_connection(bus="SESSION")


def _portal_version(connection: Any) -> int:
    body = _call(connection, version_message())
    try:
        return int(_variant(body[0]))
    except (IndexError, TypeError, ValueError) as exc:
        raise _PortalError(
            PortalStatus.UNAVAILABLE, f"portal reported an unreadable version: {exc}"
        ) from exc


def _subscribe(connection: Any, rule: Any) -> None:
    """Ask the bus to route the monitor's signals here.

    The portal emits ``Progress`` directly at our unique name, which needs no
    match rule at all — but a directed signal is an implementation detail of
    today's portal, and ``AddMatch`` is cheap insurance against it becoming a
    broadcast. It is never load-bearing, so a bus that refuses it is logged
    and ignored rather than failing the install.
    """
    try:
        _call(connection, message_bus.AddMatch(rule))
    except Exception:  # genuinely advisory
        logger.debug("AddMatch for the update monitor was refused", exc_info=True)


def _await_completion(
    connection: Any,
    queue: Any,
    progress: ProgressCallback | None,
    *,
    timeout_s: float,
    idle_timeout_s: float,
) -> PortalOutcome:
    """Pump ``Progress`` signals until one says the transaction stopped."""
    deadline = time.monotonic() + timeout_s
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise _PortalError(
                PortalStatus.FAILED, f"the update did not finish within {timeout_s:.0f}s"
            )
        try:
            message = connection.recv_until_filtered(queue, timeout=min(idle_timeout_s, remaining))
        except TimeoutError as exc:
            raise _PortalError(
                PortalStatus.FAILED,
                f"the portal stopped reporting progress for {idle_timeout_s:.0f}s",
            ) from exc
        report = parse_progress(message.body)
        if progress is not None:
            progress(report)
        if report.running:
            continue
        if report.status == PROGRESS_DONE:
            return PortalOutcome(status=PortalStatus.OK)
        if report.status == PROGRESS_EMPTY:
            return PortalOutcome(status=PortalStatus.ALREADY_CURRENT)
        # The permission-widening refusal can arrive here rather than as an
        # error reply: the portal does the work in a thread, so whatever it
        # learns after Update() has already returned comes back as a failed
        # Progress. Same meaning, same message.
        detail = report.error_message or report.error or "the portal reported a failure"
        raise _classify(report.error, detail)


def install_update(
    *,
    version: str = "",
    progress: ProgressCallback | None = None,
    connection: Any = None,
    parent_window: str = "",
    timeout_s: float = DEFAULT_TIMEOUT_S,
    idle_timeout_s: float = DEFAULT_IDLE_TIMEOUT_S,
    in_flatpak: bool | None = None,
) -> PortalOutcome:
    """Install the pending update through the portal. **Blocks; never raises.**

    ``version`` is only used for the prose. ``connection`` is an already-open
    jeepney blocking connection, for tests; production opens and closes its
    own. ``progress`` is called from *this* thread for every ``Progress``
    signal — marshal it yourself if a GUI is listening.
    """
    if not portal_supported(in_flatpak):
        return PortalOutcome(
            status=PortalStatus.UNAVAILABLE,
            version=version,
            detail="not running inside a Flatpak sandbox, or jeepney is not installed",
        )
    owns_connection = connection is None
    try:
        connection = connection if connection is not None else _open_connection()
    except Exception as exc:  # no session bus is a fallback, not a crash
        logger.debug("could not reach the session bus", exc_info=True)
        return PortalOutcome(
            status=PortalStatus.UNAVAILABLE, version=version, detail=f"no session bus: {exc}"
        )
    monitor_path: str | None = None
    try:
        portal_version = _portal_version(connection)
        if portal_version < MIN_PORTAL_VERSION:
            raise _PortalError(
                PortalStatus.UNAVAILABLE,
                f"the Flatpak portal is version {portal_version}; "
                f"UpdateMonitor needs {MIN_PORTAL_VERSION}",
            )
        monitor_path = str(_call(connection, create_update_monitor_message())[0])
        _subscribe(connection, progress_match_rule(monitor_path))
        # Filter first, send second: a Progress signal emitted between the two
        # would otherwise be dropped on the floor. send_and_get_reply feeds
        # non-reply messages through the filters while it waits, so nothing
        # arriving during Update() is lost either.
        with connection.filter(progress_filter_rule(monitor_path), bufsize=64) as queue:
            _call(connection, update_message(monitor_path, parent_window))
            outcome = _await_completion(
                connection,
                queue,
                progress,
                timeout_s=timeout_s,
                idle_timeout_s=idle_timeout_s,
            )
        logger.info("portal update finished: %s", outcome.status)
        return outcome.model_copy(update={"version": version})
    except _PortalError as exc:
        level = logging.INFO if exc.status is PortalStatus.UNAVAILABLE else logging.WARNING
        logger.log(level, "portal update did not complete (%s): %s", exc.status, exc.detail)
        return PortalOutcome(status=exc.status, version=version, detail=exc.detail)
    except Exception as exc:  # the caller gets an outcome, never a traceback
        logger.warning("portal update failed", exc_info=True)
        return PortalOutcome(status=PortalStatus.FAILED, version=version, detail=str(exc))
    finally:
        if monitor_path is not None:
            try:
                _call(connection, close_monitor_message(monitor_path))
            except Exception:  # housekeeping
                logger.debug("closing the update monitor failed", exc_info=True)
        if owns_connection:
            try:
                connection.close()
            except Exception:  # housekeeping
                logger.debug("closing the session bus connection failed", exc_info=True)


def relaunch(
    *,
    connection: Any = None,
    argv: tuple[str, ...] = (RELAUNCH_COMMAND,),
    cwd: str | None = None,
    in_flatpak: bool | None = None,
) -> bool:
    """Start a fresh sandbox instance running the newly installed version.

    Returns whether the portal accepted the request; the caller quits on True
    and says so on False. Deliberately a separate call from
    :func:`install_update`: quitting the app is the user's decision, and an
    update that installed successfully is still a success if they choose to
    finish the pull they are in first.
    """
    if not portal_supported(in_flatpak):
        return False
    owns_connection = connection is None
    try:
        connection = connection if connection is not None else _open_connection()
    except Exception:
        logger.warning("relaunch could not reach the session bus", exc_info=True)
        return False
    try:
        _call(connection, spawn_message(argv, cwd if cwd is not None else os.getcwd()))
        return True
    except Exception:  # a failed relaunch just means "quit by hand"
        logger.warning("the portal refused to relaunch nParse+", exc_info=True)
        return False
    finally:
        if owns_connection:
            try:
                connection.close()
            except Exception:  # housekeeping
                logger.debug("closing the session bus connection failed", exc_info=True)
