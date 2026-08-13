"""Sharing off silences the REST publishers too, not just the hub client.

The hub socket is the visible half of sharing; the quieter half is the ~seven
collaborators that were handed ``api``+``submit`` at construction and keep
publishing through them — the /who roster upsert and the NPC-activity post
that carries your last ``/loc``. Composition passes ``None`` for both when
sharing is off at launch, so this is about reaching that same state when the
mode changes at runtime (#69) — and on a launch with sharing off but a
pigparse dump-upload target, which builds the REST client for the uploader.
"""

from __future__ import annotations

import ast
import threading
from datetime import datetime, timedelta
from pathlib import Path

from nparseplus.composition import build_backend
from nparseplus.config.settings import Settings
from nparseplus.core.bus import EventBus
from nparseplus.core.enums import Server
from nparseplus.core.events import SlainEvent, WhoPlayer, WhoPlayerEvent
from nparseplus.core.handlers.player_tracker import PlayerTrackerHandler
from nparseplus.core.handlers.zone_activity import ZoneActivityHandler
from nparseplus.core.player import ActivePlayer
from nparseplus.core.sharing import SharingCoordinator, sharing_gated_submit
from nparseplus.core.timers import TimersService
from nparseplus.core.zones import load_zone_database
from nparseplus.net.worker import NetWorker

T0 = datetime(2026, 7, 8, 12, 0, 0)

#: The handlers composition hands the GATED submit to. Gating submit only
#: works because each of them touches ``api`` from inside the closure it
#: submits — pinned by test_the_gate_covers_every_api_call below.
SHARING_HANDLER_MODULES = (
    "player_tracker.py",
    "fte.py",
    "quake.py",
    "boat.py",
    "consider.py",
    "zone_activity.py",
    "api_timers.py",
)


class RecordingApi:
    """Records every REST call the handlers would make."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def upsert_players(self, players, server) -> None:
        self.calls.append("upsert_players")

    def players_by_names(self, names, server):
        self.calls.append("players_by_names")
        return []

    def send_npc_activity(self, **kwargs) -> None:
        self.calls.append("send_npc_activity")


def _immediate_submit(fetch, apply=None) -> None:
    """A NetWorker stand-in that runs the work inline."""
    result = fetch()
    if apply is not None:
        apply(result)


class FakeClient:
    status = "connected"

    def start(self) -> None: ...
    def stop(self) -> None: ...
    def set_server(self, server) -> None: ...
    def send_location(self, **kwargs) -> None: ...
    def send_dragon_roar(self, **kwargs) -> None: ...
    def send_waypoint(self, **kwargs) -> None: ...


def _coordinator(settings: Settings, bus: EventBus | None = None) -> SharingCoordinator:
    """A coordinator with a live client — its ``network_allowed`` is the
    predicate composition passes to the gate."""
    player = ActivePlayer()
    player.reset_for("Xantik", Server.GREEN)
    player.zone = "gfaydark"
    return SharingCoordinator(
        bus=bus if bus is not None else EventBus(),
        player=player,
        settings=settings,
        timers=TimersService(),
        last_you_activity=lambda: None,
        client=FakeClient(),
    )


# --- the gate itself ---------------------------------------------------------


def test_gate_forwards_while_sharing_is_on() -> None:
    settings = Settings()
    settings.sharing.mode = "pigparse"
    allowed = _coordinator(settings).network_allowed
    ran: list[int] = []
    gated = sharing_gated_submit(lambda fetch, apply=None: ran.append(fetch()), allowed)
    gated(lambda: 1)
    assert ran == [1]


def test_gate_drops_while_sharing_is_off() -> None:
    settings = Settings()
    settings.sharing.mode = "off"
    allowed = _coordinator(settings).network_allowed
    ran: list[int] = []
    gated = sharing_gated_submit(lambda fetch, apply=None: ran.append(fetch()), allowed)
    gated(lambda: 1)
    assert ran == []


def test_gate_reads_the_mode_per_call() -> None:
    """It is a live gate, not a snapshot — Apply changes the answer."""
    settings = Settings()
    settings.sharing.mode = "pigparse"
    allowed = _coordinator(settings).network_allowed
    ran: list[int] = []
    gated = sharing_gated_submit(lambda fetch, apply=None: ran.append(fetch()), allowed)
    gated(lambda: 1)
    settings.sharing.mode = "off"
    gated(lambda: 2)
    assert ran == [1]


def test_gate_passes_none_through() -> None:
    assert sharing_gated_submit(None, lambda: True) is None


# --- queued work: the mode is re-read where the call is actually made ----------


class DeferredWorker:
    """A NetWorker stand-in that queues instead of running — the FIFO the real
    one has, made explicit so a task can be dequeued after the mode changes."""

    def __init__(self) -> None:
        self.queued: list[tuple] = []

    def submit(self, fetch, apply=None) -> None:
        self.queued.append((fetch, apply))

    def run_all(self) -> None:
        for fetch, apply in self.queued:
            result = fetch()
            if apply is not None:
                apply(result)
        self.queued.clear()


def test_a_queued_call_is_dropped_when_it_finally_runs() -> None:
    """Submitted while sharing was on, dequeued after Apply — the request must
    not be made. One worker, one FIFO: this is a slow request away from being
    the common case, not a corner."""
    settings = Settings()
    settings.sharing.mode = "pigparse"
    worker = DeferredWorker()
    api = RecordingApi()
    gated = sharing_gated_submit(worker.submit, _coordinator(settings).network_allowed)

    gated(lambda: api.upsert_players([], 0))
    assert worker.queued, "the task was queued while sharing was on"

    settings.sharing.mode = "off"
    worker.run_all()
    assert api.calls == []


def test_a_queued_result_is_not_applied_after_the_switch() -> None:
    """The fetch got its answer just before Apply; applying it afterwards
    would mutate local state from data sharing was supposed to stop."""
    settings = Settings()
    settings.sharing.mode = "pigparse"
    worker = DeferredWorker()
    applied: list[object] = []
    gated = sharing_gated_submit(worker.submit, _coordinator(settings).network_allowed)

    gated(lambda: "roster", applied.append)
    fetch, apply = worker.queued[0]
    result = fetch()  # ran while sharing was still on
    settings.sharing.mode = "off"
    apply(result)
    assert applied == []


def test_a_queued_call_still_runs_while_sharing_stays_on() -> None:
    settings = Settings()
    settings.sharing.mode = "pigparse"
    worker = DeferredWorker()
    applied: list[object] = []
    sharing_gated_submit(worker.submit, _coordinator(settings).network_allowed)(
        lambda: "roster", applied.append
    )
    worker.run_all()
    assert applied == ["roster"]


def test_a_falsy_fetch_result_still_reaches_apply() -> None:
    """The drop sentinel must not be confused with a real empty answer —
    players_by_names returning [] is the normal case."""
    settings = Settings()
    settings.sharing.mode = "pigparse"
    worker = DeferredWorker()
    applied: list[object] = []
    sharing_gated_submit(worker.submit, _coordinator(settings).network_allowed)(
        lambda: None, applied.append
    )
    worker.run_all()
    assert applied == [None]


def test_the_real_worker_drops_a_task_stuck_behind_a_slow_one() -> None:
    """The review's scenario, on the actual NetWorker thread: a sharing call
    queued behind an in-progress request, with Apply landing in between."""
    settings = Settings()
    settings.sharing.mode = "pigparse"
    delivered: list = []
    worker = NetWorker(deliver=delivered.append)
    api = RecordingApi()

    holding = threading.Event()
    released = threading.Event()

    def slow_unrelated_request():
        holding.set()
        released.wait(timeout=5.0)

    worker.start()
    try:
        worker.submit(slow_unrelated_request)  # raw handle: an upload, say
        assert holding.wait(timeout=5.0), "the worker never picked up the first task"
        sharing_gated_submit(worker.submit, _coordinator(settings).network_allowed)(
            lambda: api.upsert_players([], 0)
        )

        settings.sharing.mode = "off"  # the user hits Apply while it waits
        released.set()
        worker.stop()  # drains the queue, then joins
    finally:
        released.set()
    assert api.calls == []


class StubSpeaker:
    def speak(self, *_a, **_k) -> None: ...
    def interrupt(self) -> None: ...
    def close(self) -> None: ...


# --- off is one-way for the session --------------------------------------------


def test_turning_sharing_back_on_does_not_resume_the_publishers() -> None:
    """off -> pigparse in the same session must resume NEITHER half.

    The hub client is stopped and dropped by apply_mode and nothing rebuilds
    it before a restart, so letting the REST publishers restart on their own
    would leave the map dark while the /who upserts resumed — half-applied in
    the other direction, and contradicting a page that says a restart is
    needed to turn sharing on.
    """
    settings = Settings()
    settings.sharing.mode = "pigparse"
    coordinator = _coordinator(settings)
    worker = DeferredWorker()
    api = RecordingApi()
    gated = sharing_gated_submit(worker.submit, coordinator.network_allowed)

    settings.sharing.mode = "off"
    assert coordinator.apply_mode() is True  # the client is stopped and dropped

    settings.sharing.mode = "pigparse"  # the user changes their mind, no restart
    gated(lambda: api.upsert_players([], 0))
    worker.run_all()
    assert api.calls == []
    assert coordinator.network_allowed() is False


def test_the_tray_says_why_rather_than_claiming_off() -> None:
    """After off -> on the status must not read "off": the user just set the
    picker to pigparse. It names the restart instead."""
    settings = Settings()
    settings.sharing.mode = "pigparse"
    coordinator = _coordinator(settings)
    settings.sharing.mode = "off"
    coordinator.apply_mode()
    assert coordinator.status == "off"

    settings.sharing.mode = "pigparse"
    assert coordinator.status == "pigparse — restart to connect"


def test_a_session_launched_off_cannot_start_publishing_either() -> None:
    """The cold-start pair: sharing off + a pigparse upload target builds the
    REST client for the uploader and no sharing client, so flipping the mode
    on mid-session must not wake the publishers."""
    settings = Settings()
    settings.sharing.mode = "off"
    settings.dumps.upload_target = "pigparse"
    backend = build_backend(settings, speaker=StubSpeaker())
    worker = backend.net_worker
    assert worker is not None and backend.player_tracker is not None

    settings.sharing.mode = "pigparse"
    backend.player_tracker.submit(lambda: "published")
    assert worker._queue.empty()
    backend.stop()


# --- the two publishers the review named --------------------------------------


def test_player_tracker_stops_upserting_the_who_roster() -> None:
    settings = Settings()
    settings.sharing.mode = "pigparse"
    bus = EventBus()
    player = ActivePlayer()
    player.reset_for("Xantik", Server.GREEN)
    api = RecordingApi()
    coordinator = _coordinator(settings, bus)
    handler = PlayerTrackerHandler(
        bus,
        player,
        api=api,
        submit=sharing_gated_submit(_immediate_submit, coordinator.network_allowed),
    )

    def who(name: str) -> None:
        bus.publish(WhoPlayerEvent(timestamp=T0, player=WhoPlayer(name=name)))

    who("Soandso")
    handler.tick(T0)
    assert api.calls == ["upsert_players", "players_by_names"]

    settings.sharing.mode = "off"
    who("Someoneelse")
    handler.tick(T0 + timedelta(minutes=5))
    assert api.calls == ["upsert_players", "players_by_names"]  # nothing new left


def test_zone_activity_stops_posting_npc_activity() -> None:
    settings = Settings()
    settings.sharing.mode = "pigparse"
    bus = EventBus()
    player = ActivePlayer()
    player.reset_for("Xantik", Server.GREEN)
    player.zone = "gfaydark"
    api = RecordingApi()
    coordinator = _coordinator(settings, bus)
    ZoneActivityHandler(
        bus,
        player,
        load_zone_database(),
        api=api,
        submit=sharing_gated_submit(_immediate_submit, coordinator.network_allowed),
    )
    bus.publish(SlainEvent(timestamp=T0, victim="a Kromzek Captain", killer="Xantik"))
    assert api.calls == ["send_npc_activity"]

    settings.sharing.mode = "off"
    bus.publish(
        SlainEvent(
            timestamp=T0 + timedelta(seconds=30), victim="a Kromzek Captain", killer="Xantik"
        )
    )
    assert api.calls == ["send_npc_activity"]  # the NPC + your last /loc stay home


# --- composition: which handle each collaborator gets --------------------------


def test_sharing_off_with_a_pigparse_upload_target_still_gates_the_publishers() -> None:
    """The case that is wrong even on a cold start: picking pigparse as the
    dump-upload destination builds the REST client and the worker, and those
    used to be handed to sharing's publishers as well."""
    settings = Settings()
    settings.sharing.mode = "off"
    settings.dumps.upload_target = "pigparse"
    backend = build_backend(settings, speaker=StubSpeaker())

    assert backend.pigparse_api is not None  # built for the uploader
    worker = backend.net_worker
    assert worker is not None
    assert backend.player_tracker is not None

    # The worker is not running here, so "did it reach the network" is "did
    # anything get queued for it".
    backend.player_tracker.submit(lambda: "published")
    assert worker._queue.empty()  # sharing is off: the tracker's submit is a no-op

    # ...while the uploader's own handle is untouched: its gate is the upload
    # target, a decision the user makes separately.
    upload = backend.inventory_upload
    assert upload is not None
    upload.submit(lambda: "uploaded", None)
    assert not worker._queue.empty()
    backend.stop()


# --- why gating submit is enough ------------------------------------------------


def test_the_gate_covers_every_api_call() -> None:
    """Every ``api`` call in a gated handler must sit inside the closure it
    hands to ``submit`` — i.e. nested at least two functions deep. A call made
    directly in a method body would run on the driver thread, never pass
    through ``submit``, and so escape the sharing-off gate entirely.
    """
    handlers = Path(__file__).resolve().parents[2] / "src" / "nparseplus" / "core" / "handlers"
    escapes: list[str] = []

    def visit(node: ast.AST, depth: int, module: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.Call):
                func = child.func
                if (
                    isinstance(func, ast.Attribute)
                    and isinstance(func.value, ast.Attribute | ast.Name)
                    and (
                        func.value.attr == "api"
                        if isinstance(func.value, ast.Attribute)
                        else func.value.id == "api"
                    )
                    and depth < 2
                ):
                    escapes.append(f"{module}:{child.lineno} {ast.unparse(func)}")
            nested = isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda)
            visit(child, depth + 1 if nested else depth, module)

    for name in SHARING_HANDLER_MODULES:
        path = handlers / name
        assert path.is_file(), f"{name} moved — update SHARING_HANDLER_MODULES"
        visit(ast.parse(path.read_text(encoding="utf-8")), 0, name)

    assert escapes == [], (
        f"api called outside a submit closure at {escapes}; "
        "sharing_gated_submit cannot silence that call"
    )


def test_that_scan_would_notice_an_escape() -> None:
    """Negative control: a call in a method body must be flagged, and the same
    call one closure deeper must not be."""
    tree = ast.parse(
        "class H:\n"
        "    def send(self):\n"
        "        self.api.send_quake(1)\n"
        "    def ok(self):\n"
        "        submit(lambda: self.api.send_quake(1))\n"
    )
    found: list[int] = []

    def visit(node: ast.AST, depth: int) -> None:
        for child in ast.iter_child_nodes(node):
            if (
                isinstance(child, ast.Call)
                and isinstance(child.func, ast.Attribute)
                and isinstance(child.func.value, ast.Attribute)
                and child.func.value.attr == "api"
                and depth < 2
            ):
                found.append(child.lineno)
            nested = isinstance(child, ast.FunctionDef | ast.Lambda)
            visit(child, depth + 1 if nested else depth)

    visit(tree, 0)
    assert found == [3]  # the bare call only; the submitted one is fine
