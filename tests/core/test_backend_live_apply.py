"""The Qt-free half of the settings window's live-apply seams.

``Backend.apply_dps_settings`` established the shape: the settings window
mutates the settings tree and calls a Backend method that pushes the change
onto whatever the app built at launch. These are the ones that joined it —
overlay durations (#67), the sharing mode (#69), the dump upload destination
(#68) and the spell database behind the EQ install directory (#70).
"""

from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path

from nparseplus.composition import build_backend
from nparseplus.config.settings import Settings

FIXTURE_SPELLS = Path(__file__).resolve().parents[1] / "fixtures" / "spells_us.txt"


class StubSpeaker:
    def speak(self, *_a, **_k) -> None: ...
    def interrupt(self) -> None: ...
    def close(self) -> None: ...


def backend_for(mode: str = "off"):
    settings = Settings()
    settings.sharing.mode = mode
    return build_backend(settings, speaker=StubSpeaker())


def test_apply_overlay_timings_retimes_the_running_trigger_engine() -> None:
    backend = backend_for()
    assert backend.trigger_engine.display_text_seconds == 4.0  # the default
    backend.settings.general.overlay_text_seconds = 12.0
    backend.apply_overlay_timings()
    assert backend.trigger_engine.display_text_seconds == 12.0


def test_apply_sharing_mode_off_stops_the_client_and_forgets_it() -> None:
    backend = backend_for("pigparse")
    client = backend.sharing_client
    assert client is not None  # built because the mode asked for it

    backend.settings.sharing.mode = "off"
    backend.apply_sharing_mode()

    assert backend.sharing_client is None  # so stop() cannot stop it twice
    assert backend.sharing.status == "off"
    # The coordinator dropped it too, so nothing outbound can reach it.
    backend.stop()


def test_apply_sharing_mode_does_not_build_a_client_when_turned_on() -> None:
    """Off -> on stays a restart (the L half of #69): ten handlers captured
    the REST client at construction, so there is nowhere to put a new one.
    This pins that the seam does not half-do it."""
    backend = backend_for("off")
    backend.settings.sharing.mode = "pigparse"
    backend.apply_sharing_mode()
    assert backend.sharing_client is None


def test_apply_dps_settings_pushes_the_new_knobs() -> None:
    """The whole DPS page reaches the tracker the app built at launch.

    The page is the only place these can be changed and the tracker outlives
    every settings window, so a knob the seam forgets is a knob that silently
    needs a restart.
    """
    backend = backend_for()
    assert backend.fights.damage_sources == "melee+mine"  # the built default

    backend.settings.dps.damage_sources = "all"
    backend.settings.dps.spell_credit_window_seconds = 5.0
    backend.settings.dps.count_pet_damage = True
    backend.settings.dps.trailing_window_seconds = 6.0
    backend.apply_dps_settings()

    assert backend.fights.damage_sources == "all"
    assert backend.fights.spell_credit_window_s == 5.0
    assert backend.fights.count_pet_damage is True
    assert backend.fights.trailing_window_s == 6.0


def test_the_dps_handler_follows_the_pet_the_app_built() -> None:
    """composition wires ONE PlayerPet; the DPS tracker must see that one."""
    backend = backend_for()
    backend.player_pet.set_name("Vexer")
    assert backend.fights.pet_name == "Vexer"
    backend.player_pet.reset()
    assert backend.fights.pet_name == ""


# --- the dump upload destination (#68) -------------------------------------------


def upload_backend(target: str = "off", mode: str = "off"):
    settings = Settings()
    settings.sharing.mode = mode
    settings.dumps.upload_target = target
    return build_backend(settings, speaker=StubSpeaker())


def test_picking_p99planner_brings_its_own_client_and_worker() -> None:
    """The acceptance case: sharing off, destination off at launch, picked
    mid-session. Before this the handler kept None and every upload no-opped."""
    backend = upload_backend()
    assert backend.inventory_upload is not None
    assert backend.inventory_upload.planner is None  # nothing was built at launch

    backend.settings.dumps.upload_target = "p99planner"
    backend.apply_upload_target()

    assert backend.planner_api is not None
    assert backend.inventory_upload.planner is backend.planner_api
    assert callable(backend.inventory_upload.submit)  # ...and it has a thread to run on
    backend.stop()


def test_picking_pigparse_builds_the_rest_client_for_a_fresh_login() -> None:
    """Logging in with Discord and picking pigparse in one Apply: the token is
    already live (it rides in a per-request header), the client was what was
    missing."""
    backend = upload_backend()
    assert backend.pigparse_api is None

    backend.settings.pigparse_account.api_token = "t0ken"
    backend.settings.dumps.upload_target = "pigparse"
    backend.apply_upload_target()

    assert backend.pigparse_api is not None
    assert backend.inventory_upload is not None
    assert backend.inventory_upload.api is backend.pigparse_api
    assert callable(backend.inventory_upload.submit)
    backend.stop()


def test_switching_destinations_never_tears_the_old_one_down() -> None:
    """stop() closes these at quit and an in-flight claim or PUT would go with
    them; "off" is already honoured by accepts() before any send, so an idle
    client is the cheaper half of the trade."""
    backend = upload_backend("p99planner")
    planner = backend.planner_api
    worker = backend.net_worker
    assert planner is not None

    backend.settings.dumps.upload_target = "off"
    backend.apply_upload_target()

    assert backend.planner_api is planner
    assert backend.net_worker is worker
    backend.stop()


def test_reapplying_the_same_destination_does_not_build_a_second_client() -> None:
    backend = upload_backend("pigparse")
    api, worker = backend.pigparse_api, backend.net_worker
    backend.apply_upload_target()
    assert backend.pigparse_api is api
    assert backend.net_worker is worker
    backend.stop()


def test_an_upload_destination_does_not_re_arm_the_sharing_handlers() -> None:
    """The narrow half of #68: a REST client built for an upload is not
    permission to publish a location (#69 owns that direction)."""
    backend = upload_backend()
    backend.settings.dumps.upload_target = "pigparse"
    backend.apply_upload_target()
    assert backend.pigparse_api is not None
    assert backend.sharing_client is None
    assert backend.sharing.status == "off"
    backend.stop()


# --- the spell database behind the EQ install directory (#70) --------------------


def test_setting_the_install_directory_schedules_a_reload(tmp_path) -> None:
    backend = upload_backend()
    assert backend.spells.source_path == Path("data/spells/spells_us.txt")  # bundled

    (tmp_path / "spells_us.txt").write_text(FIXTURE_SPELLS.read_text(), encoding="utf-8")
    backend.settings.general.eq_install_dir = tmp_path

    assert backend.reload_spell_book() is True
    assert backend.spell_reload is not None
    assert backend.spell_reload.pending == tmp_path / "spells_us.txt"


def test_an_install_without_a_spell_file_stays_on_the_bundled_database(tmp_path) -> None:
    """_spells_path falls back, so the resolved path never moved and
    re-parsing 8k lines to arrive where we already are would stall a tick."""
    backend = upload_backend()
    backend.settings.general.eq_install_dir = tmp_path  # empty

    assert backend.reload_spell_book() is False
    assert backend.spell_reload is not None
    assert backend.spell_reload.pending is None


def one_spell_database(tmp_path: Path) -> Path:
    """An install whose spells_us.txt holds exactly one spell."""
    lines = FIXTURE_SPELLS.read_text(encoding="utf-8").splitlines()
    clarity = next(line for line in lines if line.split("^")[1:2] == ["Clarity"])
    (tmp_path / "spells_us.txt").write_text(clarity + "\n", encoding="utf-8")
    return tmp_path


def test_the_driver_tick_is_what_performs_the_swap(tmp_path) -> None:
    """The book is read on the driver thread on every cast line, so apply()
    only leaves a request: one tick starts the parse off-thread, a later tick
    adopts the result. The handlers' object identity survives both, which is
    the whole reason adopt() exists."""
    backend = upload_backend()
    book = backend.spells
    assert backend.pipeline._ctx.spells is book  # what the parsers read

    backend.settings.general.eq_install_dir = one_spell_database(tmp_path)
    backend.reload_spell_book()
    assert len(book.spells) > 1  # not yet — nothing has run on the driver thread

    assert backend.spell_reload is not None
    backend.spell_reload.tick(datetime.now())  # starts the parse
    assert backend.spell_reload.pending is None  # one-shot
    assert backend.spell_reload.wait()
    backend.spell_reload.tick(datetime.now())  # adopts it

    assert backend.spells is book
    assert backend.pipeline._ctx.spells is book
    assert [spell.name for spell in book.spells] == ["Clarity"]


def test_the_parse_does_not_run_on_the_driver_thread(tmp_path) -> None:
    """~1.1 s for a full spells_us.txt — four times the budget a plugin tick
    is evicted for, on the thread that tails the log. So the tick that starts
    a reload must return without having done it."""
    backend = upload_backend()
    book = backend.spells
    backend.settings.general.eq_install_dir = one_spell_database(tmp_path)
    backend.reload_spell_book()
    assert backend.spell_reload is not None

    threads: list[str] = []
    backend.spell_reload._book = _Recorder(book, threads)  # type: ignore[assignment]
    backend.spell_reload.tick(datetime.now())
    assert threads == []  # the starting tick adopted nothing

    assert backend.spell_reload.wait()
    backend.spell_reload.tick(datetime.now())
    assert threads == ["MainThread"]  # ...the adopting one did, here


class _Recorder:
    """A SpellBook stand-in that records which thread adopt() ran on."""

    def __init__(self, book, threads: list[str]) -> None:
        self.npcs = book.npcs
        self._threads = threads

    def adopt(self, other) -> None:
        self._threads.append(threading.current_thread().name)


def test_a_bad_spell_path_does_not_break_the_tick(tmp_path) -> None:
    backend = upload_backend()
    book = backend.spells
    before = len(book.spells)
    assert backend.spell_reload is not None

    backend.spell_reload.request(tmp_path / "gone.txt")
    backend.spell_reload.tick(datetime.now())  # swallowed, logged
    assert backend.spell_reload.wait()
    backend.spell_reload.tick(datetime.now())

    assert len(book.spells) == before
    assert book.source_path == Path("data/spells/spells_us.txt")


def test_a_second_request_waits_for_the_one_in_flight(tmp_path, monkeypatch) -> None:
    """BackgroundJob refuses to start a second run, and the request must be
    kept rather than dropped — otherwise a fast double-Apply loses the second
    path and the book stays on the first one forever."""
    backend = upload_backend()
    assert backend.spell_reload is not None
    started: list[str] = []
    monkeypatch.setattr(
        backend.spell_reload._job, "_spawn", lambda name, work: started.append(name)
    )

    backend.spell_reload.request(one_spell_database(tmp_path) / "spells_us.txt")
    backend.spell_reload.tick(datetime.now())
    assert started == ["spell-reload"]

    other = tmp_path / "other"
    other.mkdir()
    backend.spell_reload.request(one_spell_database(other) / "spells_us.txt")
    backend.spell_reload.tick(datetime.now())  # refused: the first never finished

    assert started == ["spell-reload"]
    assert backend.spell_reload.pending == other / "spells_us.txt"  # kept, not dropped


# --- Mob Info's wiki lookup (#113) ----------------------------------------------


def mobinfo_backend(*, wiki_details: bool):
    settings = Settings()
    settings.sharing.mode = "off"
    settings.mobinfo.wiki_details = wiki_details
    return build_backend(settings, speaker=StubSpeaker())


def test_turning_the_wiki_lookup_on_builds_what_the_fetch_needs() -> None:
    """The handler reads the setting live; the client and the thread it
    fetches on were decided at launch, so this is the half that has to move."""
    backend = mobinfo_backend(wiki_details=False)
    assert backend.wiki is None and backend.net_worker is None
    con = backend.con_handler
    assert con is not None and con.wiki is None and con.wiki_submit is None

    backend.settings.mobinfo.wiki_details = True
    backend.apply_mobinfo_settings()

    assert backend.wiki is not None
    assert con.wiki is backend.wiki
    assert con.wiki_submit is not None
    backend.stop()


def test_turning_it_off_stops_the_fetch_without_tearing_anything_down() -> None:
    backend = mobinfo_backend(wiki_details=True)
    client, worker = backend.wiki, backend.net_worker
    con = backend.con_handler
    assert client is not None and worker is not None and con is not None

    backend.settings.mobinfo.wiki_details = False
    backend.apply_mobinfo_settings()

    assert con.wiki is None and con.wiki_submit is None  # nothing can fetch
    assert backend.wiki is client and backend.net_worker is worker  # still idle
    backend.stop()


def test_reapplying_does_not_build_a_second_client() -> None:
    backend = mobinfo_backend(wiki_details=True)
    client, worker = backend.wiki, backend.net_worker
    backend.apply_mobinfo_settings()
    assert backend.wiki is client and backend.net_worker is worker
    backend.stop()


def test_the_wiki_lookup_needs_no_sharing_and_grants_none() -> None:
    """wiki.project1999.com is not pigparse's API: the lookup brings its own
    worker with sharing off, and that worker is not permission to publish."""
    backend = mobinfo_backend(wiki_details=True)
    assert backend.net_worker is not None
    assert backend.sharing_client is None
    assert backend.pigparse_api is None
    assert backend.sharing.pigparse_rest_allowed() is False
    backend.stop()


def test_the_picture_toggle_reaches_a_running_handler() -> None:
    backend = mobinfo_backend(wiki_details=True)
    con = backend.con_handler
    assert con is not None and con.want_image() is True
    backend.settings.mobinfo.show_image = False
    assert con.want_image() is False  # read at fetch time, not at construction
    backend.stop()
