"""Hub client reconnect machine + decode tests, driven synchronously.

A scripted FakeHubTransport runs the client's ``_run`` loop on the test
thread: transports fire ``on_close`` inline, so every state transition is
deterministic — no real sockets, no real time (``sleep`` is recorded).
"""

import random
import threading

from nparseplus.core.events import (
    CustomTimerReceivedRemoteEvent,
    DragonRoarRemoteEvent,
    OtherPlayerLocationReceivedRemoteEvent,
    PlayerDisconnectReceivedRemoteEvent,
)
from nparseplus.core.geometry import Loc
from nparseplus.net import hubproto
from nparseplus.net.pigparse_hub import CONNECT_FAIL_WAIT_S, PigParseHubClient, RawWsTransport

CAMEL_PLAYER = {
    "name": "Soandso",
    "guildName": "Bregan D'Aerth",
    "sharing": 0,
    "server": 0,
    "zone": "gfaydark",
    "x": 1.0,
    "y": 2.0,
    "z": 3.0,
    "trackingDistance": None,
    "groupName": "Green_gfaydark",
}


class FakeHubTransport:
    """Scripted transport. ``plan`` entries: "fail" (connect raises),
    "drop" (connect ok, session closes as soon as it is held), or
    "hold" (connect ok, stays up until the test closes it)."""

    def __init__(self, url: str, behavior: str, log: list) -> None:
        self.url = url
        self.behavior = behavior
        self.log = log
        self.sent: list[tuple[str, list]] = []
        self.closed = False
        self.on_invocation = lambda target, args: None
        self.on_close = lambda exc: None

    def connect(self) -> None:
        if self.behavior == "fail":
            self.log.append("connect-failed")
            raise ConnectionError("scripted failure")
        self.log.append("connected")

    def send_invocation(self, target: str, arguments: list) -> None:
        self.sent.append((target, arguments))
        self.log.append(f"send:{target}")
        self._maybe_drop()

    def invoke(self, target: str, arguments: list, timeout: float) -> None:
        self.sent.append((target, arguments))
        self.log.append(f"send:{target}")
        if self.behavior == "join-fail":
            raise TimeoutError("scripted setup timeout")

    def send_ping(self) -> None:
        self.log.append("ping")
        self._maybe_drop()

    def _maybe_drop(self) -> None:
        if self.behavior == "drop":
            # Session dies on the first traffic (invocation or ping).
            self.behavior = "hold"
            self.on_close(ConnectionError("scripted drop"))

    def close(self) -> None:
        self.closed = True
        self.on_close(None)


class Harness:
    def __init__(self, plan: list[str], server: int | None = 0) -> None:
        self.log: list = []
        self.sleeps: list[float] = []
        self.transports: list[FakeHubTransport] = []
        self.inbound: list = []
        plan_iter = iter(plan)

        def factory(url: str) -> FakeHubTransport:
            try:
                behavior = next(plan_iter)
            except StopIteration:
                # Script exhausted: tell the client to stop instead of
                # connecting again.
                self.client._stop.set()
                behavior = "fail"
            transport = FakeHubTransport(url, behavior, self.log)
            self.transports.append(transport)
            return transport

        def sleep(seconds: float) -> None:
            self.sleeps.append(seconds)

        self.client = PigParseHubClient(
            url="https://hub.test/PP",
            on_inbound=self.inbound.append,
            transport_factory=factory,
            rng=random.Random(42),
            sleep=sleep,
            ping_interval_s=0.001,
        )
        if server is not None:
            self.client.set_server(server)

    def run(self) -> None:
        """Run the connection loop synchronously to script exhaustion."""
        self.client._run()


def test_joins_group_after_every_reconnect() -> None:
    h = Harness(plan=["drop", "drop"])
    h.run()
    joins = [entry for entry in h.log if entry == "send:JoinServerGroup"]
    assert len(joins) == 2  # once per (re)connect
    assert h.transports[0].sent[0] == ("JoinServerGroup", [0])
    assert h.transports[1].sent[0] == ("JoinServerGroup", [0])
    # Post-drop delays came from the seeded rng jitter (0..4s), not the
    # 5s connect-failure wait.
    assert all(0 <= s <= 4 for s in h.sleeps)


def test_connect_failure_waits_flat_5s() -> None:
    h = Harness(plan=["fail", "fail"])
    h.run()
    assert h.sleeps == [CONNECT_FAIL_WAIT_S, CONNECT_FAIL_WAIT_S]
    assert "connected" not in h.log


def test_no_join_without_server() -> None:
    h = Harness(plan=["drop"], server=None)
    assert h.client._connect_once() is None
    assert h.client.status == "waiting for character"
    assert not any(entry.startswith("send:") for entry in h.log)


def _send_test_location(client: PigParseHubClient) -> None:
    client.send_location(
        name="Soandso",
        guild_name="Bregan D'Aerth",
        server=0,
        zone="gfaydark",
        sharing=0,
        # "Your Location is 111, 222, 3" -> Loc(x=222, y=111, z=3)
        loc=Loc(x=222.0, y=111.0, z=3.0),
    )


def test_sends_dropped_unless_connected() -> None:
    h = Harness(plan=[])
    _send_test_location(h.client)  # never started: silently dropped
    h.client.send_dragon_roar(
        spell_name="Dragon Roar", guild_name=None, server=0, zone="gfaydark", sharing=0, loc=None
    )
    assert h.transports == []


def test_send_location_serializes_pascal_case_with_axis_swap() -> None:
    h = Harness(plan=["hold"])
    transport = h.client._connect_once()
    assert transport is not None
    _send_test_location(h.client)
    target, args = transport.sent[-1]
    assert target == "PlayerLocationEvent"
    assert args[0]["Name"] == "Soandso"
    assert args[0]["Zone"] == "gfaydark"
    # EQTool wire order is the raw /loc print order: X=first=111, Y=second=222.
    assert (args[0]["X"], args[0]["Y"], args[0]["Z"]) == (111.0, 222.0, 3.0)
    assert "name" not in args[0]
    transport.close()


def test_send_dragon_roar_serializes_pascal_case() -> None:
    h = Harness(plan=["hold"])
    transport = h.client._connect_once()
    assert transport is not None
    h.client.send_dragon_roar(
        spell_name="Dragon Roar",
        guild_name="",
        server=0,
        zone="permafrost",
        sharing=0,
        loc=Loc(x=222.0, y=111.0, z=3.0),
    )
    target, args = transport.sent[-1]
    assert target == "DragonRoarEvent"
    assert args[0]["SpellName"] == "Dragon Roar"
    assert args[0]["GuildName"] is None  # blank guild -> null like C#
    assert (args[0]["X"], args[0]["Y"], args[0]["Z"]) == (111.0, 222.0, 3.0)
    transport.close()


def test_status_transitions() -> None:
    h = Harness(plan=["hold"])
    assert h.client.status == "stopped"
    transport = h.client._connect_once()
    assert transport is not None
    assert h.client.status == "connected"
    transport.close()
    h.run()  # script exhausted immediately -> loop stops
    assert h.client.status == "stopped"


def test_inbound_decoding_all_targets() -> None:
    h = Harness(plan=["hold"])
    transport = h.client._connect_once()
    assert transport is not None

    transport.on_invocation("PlayerLocationEvent", [CAMEL_PLAYER])
    transport.on_invocation("PlayerDisconnected", [CAMEL_PLAYER])
    transport.on_invocation(
        "DragonRoarEvent",
        [{"spellName": "Dragon Roar", "server": 0, "zone": "permafrost", "x": 1, "y": 2, "z": 3}],
    )
    transport.on_invocation(
        "AddCustomTrigger",
        [{"name": "Kael Faction Pull In Progress", "durationInSeconds": 90, "server": 0}],
    )

    location, disconnect, roar, timer = h.inbound
    assert isinstance(location, OtherPlayerLocationReceivedRemoteEvent)
    assert location.player.name == "Soandso" and location.player.x == 1.0
    assert isinstance(disconnect, PlayerDisconnectReceivedRemoteEvent)
    assert isinstance(roar, DragonRoarRemoteEvent)
    assert roar.location is not None and roar.location.x == 1.0
    assert roar.server == 0
    assert isinstance(timer, CustomTimerReceivedRemoteEvent)
    assert timer.duration_in_seconds == 90
    transport.close()


def test_inbound_junk_is_dropped_not_raised() -> None:
    h = Harness(plan=["hold"])
    transport = h.client._connect_once()
    assert transport is not None
    transport.on_invocation("PlayerLocationEvent", [])  # no args
    transport.on_invocation("PlayerLocationEvent", ["not a dict"])
    transport.on_invocation("PlayerLocationEvent", [{"zone": 12}])  # missing name
    transport.on_invocation("SomeUnknownTarget", [{"name": "x"}])
    assert h.inbound == []
    transport.close()


def test_dragon_roar_without_full_coords_has_no_location() -> None:
    h = Harness(plan=["hold"])
    transport = h.client._connect_once()
    assert transport is not None
    transport.on_invocation("DragonRoarEvent", [{"spellName": "Dragon Roar", "server": 1}])
    (roar,) = h.inbound
    assert roar.location is None and roar.server == 1
    transport.close()


def test_set_server_reconnects_to_avoid_stale_group_membership() -> None:
    h = Harness(plan=["hold", "hold"])
    transport = h.client._connect_once()
    assert transport is not None
    h.client.set_server(1)  # Green -> Blue while connected
    assert transport.closed is True
    assert ("JoinServerGroup", [1]) not in transport.sent

    replacement = h.client._connect_once()
    assert replacement is not None
    assert replacement.sent[0] == ("JoinServerGroup", [1])
    h.client.set_server(1)  # unchanged: keep the new connection
    assert replacement.closed is False
    replacement.close()


def test_join_must_be_acknowledged_before_connected() -> None:
    h = Harness(plan=["join-fail"])
    assert h.client._connect_once() is None
    assert not h.client._connected.is_set()
    assert h.transports[0].closed is True


def test_raw_transport_waits_for_completion_frame() -> None:
    transport = RawWsTransport("https://hub.test/PP")
    sent: list[str] = []
    frame_sent = threading.Event()
    errors: list[Exception] = []

    def send_raw(frame: str) -> None:
        sent.append(frame)
        frame_sent.set()

    transport._send_raw = send_raw  # type: ignore[method-assign]

    def invoke() -> None:
        try:
            transport.invoke("JoinServerGroup", [0], timeout=1.0)
        except Exception as exc:  # pragma: no cover - assertion records it
            errors.append(exc)

    thread = threading.Thread(target=invoke)
    thread.start()
    assert frame_sent.wait(1.0)
    (message,) = hubproto.decode_frames(sent[0])
    transport._handle_completion(
        {"type": hubproto.MSG_COMPLETION, "invocationId": message["invocationId"]}
    )
    thread.join(timeout=1.0)

    assert errors == []
    assert not thread.is_alive()
