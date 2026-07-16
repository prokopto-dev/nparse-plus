"""Hub client reconnect machine + decode tests, driven synchronously.

A scripted FakeHubTransport runs the client's ``_run`` loop on the test
thread: transports fire ``on_close`` inline, so every state transition is
deterministic — no real sockets, no real time (``sleep`` is recorded).
"""

import random

from nparseplus.core.events import (
    CustomTimerReceivedRemoteEvent,
    DragonRoarRemoteEvent,
    OtherPlayerLocationReceivedRemoteEvent,
    PlayerDisconnectReceivedRemoteEvent,
)
from nparseplus.net.pigparse_hub import CONNECT_FAIL_WAIT_S, PigParseHubClient
from nparseplus.net.pigparse_models import WireDragonRoar, WirePlayer

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
    h.run()
    assert not any(entry.startswith("send:") for entry in h.log)


def test_sends_dropped_unless_connected() -> None:
    h = Harness(plan=[])
    wire = WirePlayer.model_validate(CAMEL_PLAYER)
    h.client.send_location(wire)  # never started: silently dropped
    h.client.send_dragon_roar(WireDragonRoar(spell_name="Dragon Roar"))
    assert h.transports == []


def test_send_location_serializes_pascal_case() -> None:
    h = Harness(plan=["hold"])
    transport = h.client._connect_once()
    assert transport is not None
    h.client.send_location(WirePlayer.model_validate(CAMEL_PLAYER))
    target, args = transport.sent[-1]
    assert target == "PlayerLocationEvent"
    assert args[0]["Name"] == "Soandso"
    assert args[0]["Zone"] == "gfaydark"
    assert "name" not in args[0]
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


def test_set_server_rejoins_live_connection() -> None:
    h = Harness(plan=["hold"])
    transport = h.client._connect_once()
    assert transport is not None
    h.client.set_server(1)  # Green -> Blue while connected
    assert ("JoinServerGroup", [1]) in transport.sent
    h.client.set_server(1)  # unchanged: no duplicate join
    assert transport.sent.count(("JoinServerGroup", [1])) == 1
    transport.close()
