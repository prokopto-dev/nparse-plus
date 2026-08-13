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
from nparseplus.core.sharing import sharing_gated_submit
from nparseplus.core.zones import load_zone_database

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


# --- the gate itself ---------------------------------------------------------


def test_gate_forwards_while_sharing_is_on() -> None:
    settings = Settings()
    settings.sharing.mode = "pigparse"
    ran: list[int] = []
    gated = sharing_gated_submit(lambda fetch, apply=None: ran.append(fetch()), settings)
    gated(lambda: 1)
    assert ran == [1]


def test_gate_drops_while_sharing_is_off() -> None:
    settings = Settings()
    settings.sharing.mode = "off"
    ran: list[int] = []
    gated = sharing_gated_submit(lambda fetch, apply=None: ran.append(fetch()), settings)
    gated(lambda: 1)
    assert ran == []


def test_gate_reads_the_mode_per_call() -> None:
    """It is a live gate, not a snapshot — Apply changes the answer."""
    settings = Settings()
    settings.sharing.mode = "pigparse"
    ran: list[int] = []
    gated = sharing_gated_submit(lambda fetch, apply=None: ran.append(fetch()), settings)
    gated(lambda: 1)
    settings.sharing.mode = "off"
    gated(lambda: 2)
    assert ran == [1]


def test_gate_passes_none_through() -> None:
    assert sharing_gated_submit(None, Settings()) is None


# --- the two publishers the review named --------------------------------------


def test_player_tracker_stops_upserting_the_who_roster() -> None:
    settings = Settings()
    settings.sharing.mode = "pigparse"
    bus = EventBus()
    player = ActivePlayer()
    player.reset_for("Xantik", Server.GREEN)
    api = RecordingApi()
    handler = PlayerTrackerHandler(
        bus, player, api=api, submit=sharing_gated_submit(_immediate_submit, settings)
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
    ZoneActivityHandler(
        bus,
        player,
        load_zone_database(),
        api=api,
        submit=sharing_gated_submit(_immediate_submit, settings),
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


class StubSpeaker:
    def speak(self, *_a, **_k) -> None: ...
    def interrupt(self) -> None: ...
    def close(self) -> None: ...


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
