"""InventoryUploadHandler — routing inventory dumps to the chosen site.

The EQ directory is polled once (by ``DumpWatcher``); this handler hangs off
its ``on_fresh_dump`` hook — NOT the library's bus events, which describe
local history and are the wrong question for "should this be published".
These tests drive the real watcher rather than calling the hook by hand, so
the two halves are checked joined up.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from nparseplus.core.bus import EventBus
from nparseplus.core.dumps import DumpKind, DumpLibrary, DumpWatcher, build_dump
from nparseplus.core.enums import Server
from nparseplus.core.handlers.inventory_upload import InventoryUploadHandler, export_filename
from nparseplus.core.p99planner import ClaimLink, ImportFile, UploadOutcome
from nparseplus.core.player import ActivePlayer
from nparseplus.net.worker import ImmediateWorker

T0 = datetime(2026, 8, 9, 12, 0, 0)

DUMP = (
    "Location\tName\tID\tCount\tSlots\n"
    "Charm\tGuise of the Deceiver\t1234\t1\t0\n"
    "General1-Slot1\tRusty Sword\t5678\t1\t0\n"
)
CHANGED_DUMP = DUMP.replace("Rusty Sword", "Shiny Sword")
SPELLBOOK = "51\tSuperior Healing\n14\tSpirit of Wolf\n"


class FakeApi:
    def __init__(self) -> None:
        self.uploads: list[dict] = []

    def upload_inventory(self, **kwargs) -> None:
        self.uploads.append(kwargs)


class FakePlanner:
    """Stands in for p99planner.com, recording the calls in order."""

    def __init__(self) -> None:
        self.staged: list[list[ImportFile]] = []
        self.added: list[tuple[str, list[ImportFile]]] = []
        self.released: list[str] = []
        self.gone = False  # next add() answers 410
        self.error = ""  # next call fails with this
        self._n = 0

    def stage(self, files: list[ImportFile]) -> UploadOutcome:
        if self.error:
            return UploadOutcome(error=self.error)
        self.staged.append(list(files))
        self._n += 1
        return UploadOutcome(
            link=ClaimLink(
                token=f"token{self._n}",
                url=f"https://p99planner.com/import/token{self._n}",
                expires=T0 + timedelta(hours=24),
                files=len(files),
            )
        )

    def add(self, token: str, files: list[ImportFile]) -> UploadOutcome:
        if self.gone:
            return UploadOutcome(gone=True, error="that claim link is no longer valid")
        if self.error:
            return UploadOutcome(error=self.error)
        self.added.append((token, list(files)))
        return UploadOutcome(
            link=ClaimLink(
                token=token,
                url=f"https://p99planner.com/import/{token}",
                expires=T0 + timedelta(hours=24),
                files=len(files),
            )
        )

    def release(self, token: str) -> None:
        self.released.append(token)


class Env:
    def __init__(self, tmp_path: Path, target: str = "pigparse", **overrides) -> None:
        self.eq_dir = tmp_path / "eq"
        self.eq_dir.mkdir(parents=True)
        self.api = FakeApi()
        self.planner = FakePlanner()
        self.opened: list[str] = []
        self.bus = EventBus()
        self.state = {"target": target, "token": "tok"}
        self.library = DumpLibrary(tmp_path / "library")
        self.player = ActivePlayer()
        self.player.reset_for("Xantik", Server.GREEN)
        kwargs = dict(
            get_target=lambda: self.state["target"],
            get_token=lambda: self.state["token"],
            session_start=T0,
            api=self.api,
            planner=self.planner,
            submit=ImmediateWorker().submit,
            open_browser=self.opened.append,
        )
        kwargs.update(overrides)
        self.handler = InventoryUploadHandler(self.bus, self.player, self.library, **kwargs)
        self.watcher = DumpWatcher(
            self.library,
            get_eq_dir=lambda: self.eq_dir,
            is_enabled=lambda: True,
            is_update_enabled=lambda: self.state.get("auto_update", True),
            bus=self.bus,
            # The upload trigger is this hook, NOT the library's bus events.
            on_fresh_dump=self.handler.on_fresh_dump,
        )

    def dump(self, name: str, text: str, when: datetime) -> Path:
        path = self.eq_dir / name
        path.write_text(text, encoding="utf-8")
        os.utime(path, (when.timestamp(), when.timestamp()))
        return path

    def store(self, character: str, text: str, when: datetime):
        dump = build_dump(text, character=character, kind=DumpKind.INVENTORY, captured_at=when)
        assert dump is not None
        self.library.store(dump)
        return dump


@pytest.fixture
def env(tmp_path: Path) -> Env:
    return Env(tmp_path)


@pytest.fixture
def planner_env(tmp_path: Path) -> Env:
    return Env(tmp_path, target="p99planner")


# --- pigparse ---------------------------------------------------------------


def test_a_dump_taken_this_session_uploads(env: Env) -> None:
    env.dump("Xantik-Inventory.txt", DUMP, T0 + timedelta(minutes=1))
    env.watcher.tick(T0 + timedelta(minutes=1))

    assert len(env.api.uploads) == 1
    upload = env.api.uploads[0]
    assert upload["character_name"] == "Xantik"
    assert upload["server"] == int(Server.GREEN)
    assert upload["api_token"] == "tok"
    assert [item.name for item in upload["items"]] == ["Guise of the Deceiver", "Rusty Sword"]


def test_a_dump_from_before_this_session_does_not_upload(env: Env) -> None:
    """Replaces the old watcher's startup priming, keyed on capture time."""
    env.dump("Xantik-Inventory.txt", DUMP, T0 - timedelta(days=1))
    env.watcher.tick(T0)

    assert env.api.uploads == []
    # ...but it is still collected into the library, which is the point.
    assert env.library.characters() == ["Xantik"]


def test_rerunning_outputfile_unchanged_does_not_reupload(env: Env) -> None:
    env.dump("Xantik-Inventory.txt", DUMP, T0 + timedelta(minutes=1))
    env.watcher.tick(T0 + timedelta(minutes=1))
    env.dump("Xantik-Inventory.txt", DUMP, T0 + timedelta(minutes=5))
    env.watcher.tick(T0 + timedelta(minutes=5))
    assert len(env.api.uploads) == 1


def test_a_changed_dump_uploads_again(env: Env) -> None:
    env.dump("Xantik-Inventory.txt", DUMP, T0 + timedelta(minutes=1))
    env.watcher.tick(T0 + timedelta(minutes=1))
    env.dump("Xantik-Inventory.txt", CHANGED_DUMP, T0 + timedelta(minutes=5))
    env.watcher.tick(T0 + timedelta(minutes=5))

    assert len(env.api.uploads) == 2
    assert [item.name for item in env.api.uploads[1]["items"]][-1] == "Shiny Sword"


def test_spellbook_dumps_are_not_uploaded(env: Env) -> None:
    env.dump("Xantik-Spellbook.txt", SPELLBOOK, T0 + timedelta(minutes=1))
    env.watcher.tick(T0 + timedelta(minutes=1))
    assert env.api.uploads == []
    assert env.planner.staged == []


def test_the_dump_file_names_the_character(tmp_path: Path) -> None:
    """A deliberate divergence from the C#, which uploads under whoever is
    logged in — the filename knows whose inventory this actually is."""
    env = Env(tmp_path)
    env.dump("Beeta-Inventory.txt", DUMP, T0 + timedelta(minutes=1))
    env.watcher.tick(T0 + timedelta(minutes=1))
    assert env.api.uploads[0]["character_name"] == "Beeta"
    assert env.api.uploads[0]["server"] == int(Server.GREEN)  # still the live server


def test_pigparse_needs_a_token(tmp_path: Path) -> None:
    env = Env(tmp_path)
    env.state["token"] = ""
    env.dump("Xantik-Inventory.txt", DUMP, T0 + timedelta(minutes=1))
    env.watcher.tick(T0 + timedelta(minutes=1))
    assert env.api.uploads == []


# --- upload provenance is independent of the local history gate --------------


def test_auto_update_off_does_not_suppress_uploads(tmp_path: Path) -> None:
    """Retention is a local choice; it must not decide what leaves the machine.

    The bad case in full: a stale dump is imported at startup (correctly not
    uploaded), which means a snapshot now exists — so with auto_update off,
    every later in-session dump was "skipped" and never uploaded again.
    """
    env = Env(tmp_path)
    env.state["auto_update"] = False
    env.dump("Xantik-Inventory.txt", DUMP, T0 - timedelta(days=3))
    env.watcher.tick(T0)
    assert env.api.uploads == []  # stale, correctly not uploaded
    assert env.library.snapshots("Xantik", DumpKind.INVENTORY)  # but kept

    env.dump("Xantik-Inventory.txt", CHANGED_DUMP, T0 + timedelta(minutes=5))
    env.watcher.tick(T0 + timedelta(seconds=25))

    assert len(env.api.uploads) == 1, "a fresh in-session dump must still upload"
    # ...and the retention choice is still honoured locally.
    assert len(env.library.snapshots("Xantik", DumpKind.INVENTORY)) == 1


def test_a_hand_picked_import_is_never_uploaded(tmp_path: Path) -> None:
    """Import file… files a dump away; it does not publish it.

    The file may be a backup, an export off another machine, or another
    player's dump — none of which anyone asked to send to a website by
    picking it out of a file dialog. Uploading is the Upload inventory
    button, which says so.
    """
    env = Env(tmp_path)
    someone_else = tmp_path / "SomeoneElse-Inventory.txt"
    someone_else.write_text(DUMP, encoding="utf-8")
    fresh = (T0 + timedelta(minutes=5)).timestamp()
    os.utime(someone_else, (fresh, fresh))

    env.watcher.request_import(someone_else)
    env.watcher.tick(T0 + timedelta(minutes=5))

    assert env.library.characters() == ["SomeoneElse"]  # filed away
    assert env.api.uploads == []  # but never published
    assert env.planner.staged == []


def test_a_hand_picked_import_of_your_own_dump_is_also_not_uploaded(tmp_path: Path) -> None:
    """No exception for a file that happens to be yours — the window cannot
    tell, and the manual button is right there."""
    env = Env(tmp_path)
    mine = tmp_path / "Xantik-Inventory.txt"
    mine.write_text(DUMP, encoding="utf-8")
    fresh = (T0 + timedelta(minutes=5)).timestamp()
    os.utime(mine, (fresh, fresh))

    env.watcher.request_import(mine)
    env.watcher.tick(T0 + timedelta(minutes=5))
    assert env.api.uploads == []

    # ...and asking explicitly still works.
    stored = env.library.load_latest("Xantik", DumpKind.INVENTORY)
    assert stored is not None
    env.handler.upload_now([stored])
    assert len(env.api.uploads) == 1


def test_a_directory_rescan_does_upload(tmp_path: Path) -> None:
    """Import now rescans the EQ directory — the same files the automatic
    scan would take seconds later, from the same trusted source."""
    env = Env(tmp_path)
    env.dump("Xantik-Inventory.txt", DUMP, T0 + timedelta(minutes=1))

    env.watcher.request_scan()
    env.watcher.tick(T0 + timedelta(minutes=1))

    assert len(env.api.uploads) == 1


# --- the target gate --------------------------------------------------------


def test_target_off_uploads_nowhere(tmp_path: Path) -> None:
    env = Env(tmp_path, target="off")
    env.dump("Xantik-Inventory.txt", DUMP, T0 + timedelta(minutes=1))
    env.watcher.tick(T0 + timedelta(minutes=1))
    assert env.api.uploads == []
    assert env.planner.staged == []


def test_only_the_chosen_destination_receives(planner_env: Env) -> None:
    planner_env.dump("Xantik-Inventory.txt", DUMP, T0 + timedelta(minutes=1))
    planner_env.watcher.tick(T0 + timedelta(minutes=1))
    assert planner_env.api.uploads == []  # pigparse untouched
    assert len(planner_env.planner.staged) == 1


# --- p99planner -------------------------------------------------------------


def test_staging_sends_the_raw_export_and_opens_the_review_page(planner_env: Env) -> None:
    planner_env.dump("Xantik-Inventory.txt", DUMP, T0 + timedelta(minutes=1))
    planner_env.watcher.tick(T0 + timedelta(minutes=1))

    assert len(planner_env.planner.staged) == 1
    file = planner_env.planner.staged[0][0]
    # The filename is what p99planner derives the character from, so an
    # export named anything else would create a duplicate character.
    assert file.name == "Xantik-Inventory.txt"
    assert file.text.startswith("Location\tName\tID\tCount\tSlots\n")
    assert "Guise of the Deceiver" in file.text
    # Opened, not printed: the URL is a bearer secret.
    assert planner_env.opened == ["https://p99planner.com/import/token1"]


def test_later_dumps_join_the_same_claim_link(planner_env: Env) -> None:
    """One link per session, not one per mule — and the browser opens once."""
    planner_env.dump("Xantik-Inventory.txt", DUMP, T0 + timedelta(minutes=1))
    planner_env.watcher.tick(T0 + timedelta(minutes=1))
    planner_env.dump("Bankmule-Inventory.txt", DUMP, T0 + timedelta(minutes=2))
    planner_env.watcher.tick(T0 + timedelta(minutes=2))

    assert len(planner_env.planner.staged) == 1
    assert len(planner_env.planner.added) == 1
    token, files = planner_env.planner.added[0]
    assert token == "token1"
    assert [file.name for file in files] == ["Bankmule-Inventory.txt"]
    assert planner_env.opened == ["https://p99planner.com/import/token1"]  # only once


def test_a_claimed_link_is_replaced_with_a_fresh_one(planner_env: Env) -> None:
    """410 means approved or expired: mint a new claim and hand it over."""
    planner_env.dump("Xantik-Inventory.txt", DUMP, T0 + timedelta(minutes=1))
    planner_env.watcher.tick(T0 + timedelta(minutes=1))
    planner_env.planner.gone = True

    planner_env.dump("Xantik-Inventory.txt", CHANGED_DUMP, T0 + timedelta(minutes=5))
    planner_env.watcher.tick(T0 + timedelta(minutes=5))

    assert len(planner_env.planner.staged) == 2
    assert planner_env.opened == [
        "https://p99planner.com/import/token1",
        "https://p99planner.com/import/token2",
    ]


def test_an_expired_claim_is_not_reused(planner_env: Env) -> None:
    planner_env.dump("Xantik-Inventory.txt", DUMP, T0 + timedelta(minutes=1))
    planner_env.watcher.tick(T0 + timedelta(minutes=1))
    # Age the claim past its 24h window.
    planner_env.handler._claim = planner_env.handler._claim.model_copy(
        update={"expires": datetime.now() - timedelta(minutes=1)}
    )
    planner_env.handler.upload_now([planner_env.store("Xantik", CHANGED_DUMP, T0)])

    assert len(planner_env.planner.staged) == 2
    assert planner_env.planner.added == []


def test_a_failed_stage_is_reported_and_leaves_no_claim(planner_env: Env) -> None:
    planner_env.planner.error = "could not reach p99planner.com"
    planner_env.handler.upload_now([planner_env.store("Xantik", DUMP, T0)])

    assert planner_env.opened == []
    assert planner_env.handler.claim_url() == ""
    assert "could not reach p99planner.com" in planner_env.handler.status_text()


def test_the_claim_url_never_reaches_the_status_line(planner_env: Env) -> None:
    """status_text goes on screen and into no logs — but it is one string
    away from the secret, so pin it."""
    planner_env.handler.upload_now([planner_env.store("Xantik", DUMP, T0)])
    status = planner_env.handler.status_text()
    assert "p99planner.com/import" not in status
    assert "token1" not in status
    # Nor does repr, which is what a stray log call would reach for.
    assert "token1" not in repr(planner_env.handler._claim)


def test_a_pending_claim_is_describable_without_the_url(planner_env: Env) -> None:
    """The UI needs to say "something is waiting" and offer a Review button
    without ever receiving the secret."""
    assert planner_env.handler.has_claim() is False
    assert planner_env.handler.claim_summary() == ""

    planner_env.handler.upload_now([planner_env.store("Xantik", DUMP, T0)])

    assert planner_env.handler.has_claim() is True
    summary = planner_env.handler.claim_summary()
    assert "waiting for approval" in summary
    assert "expires" in summary
    assert "p99planner.com/import" not in summary
    assert "token1" not in summary


def test_a_browser_that_returns_false_counts_as_failure(tmp_path: Path) -> None:
    """webbrowser.open RETURNS False when it has nowhere to go — it does not
    raise. That is the normal headless outcome, so it is the case the
    recovery hint most needs to cover."""
    env = Env(tmp_path, target="p99planner", open_browser=lambda _url: False)
    env.handler.upload_now([env.store("Xantik", DUMP, T0)])

    assert len(env.planner.staged) == 1  # staged fine; only the opening failed
    assert "Copy review link" in env.handler.claim_summary()
    assert env.handler.open_claim() is False


def test_an_opener_returning_none_is_not_a_failure(planner_env: Env) -> None:
    """The injected doubles (and plenty of real callables) return None. Only
    an explicit False means "no browser took it"."""
    planner_env.handler.upload_now([planner_env.store("Xantik", DUMP, T0)])
    assert "Copy review link" not in planner_env.handler.claim_summary()
    assert planner_env.handler.open_claim() is True


def test_a_browser_that_will_not_open_leaves_a_visible_way_out(tmp_path: Path) -> None:
    """The one-shot failure message is replaced by the pending line within a
    second, so the recovery hint has to live on the pending line itself."""

    def refuses(_url: str) -> None:
        raise OSError("no browser configured")

    env = Env(tmp_path, target="p99planner", open_browser=refuses)
    env.handler.upload_now([env.store("Xantik", DUMP, T0)])

    # Staged despite the browser: the exports are safe, just unreachable.
    assert len(env.planner.staged) == 1
    assert env.handler.has_claim() is True

    summary = env.handler.claim_summary()
    assert "Copy review link" in summary
    assert "waiting for approval" in summary
    assert "p99planner.com/import" not in summary  # still never the URL


def test_the_hint_clears_once_a_browser_works(planner_env: Env) -> None:
    planner_env.handler._browser_ok = False
    planner_env.handler.upload_now([planner_env.store("Xantik", DUMP, T0)])
    assert "Copy review link" not in planner_env.handler.claim_summary()


def test_an_expired_claim_stops_being_pending(planner_env: Env) -> None:
    planner_env.handler.upload_now([planner_env.store("Xantik", DUMP, T0)])
    planner_env.handler._claim = planner_env.handler._claim.model_copy(
        update={"expires": datetime.now() - timedelta(seconds=1)}
    )
    assert planner_env.handler.has_claim() is False
    assert planner_env.handler.claim_summary() == ""


def test_open_and_forget_the_pending_claim(planner_env: Env) -> None:
    planner_env.handler.upload_now([planner_env.store("Xantik", DUMP, T0)])
    planner_env.opened.clear()

    assert planner_env.handler.open_claim() is True
    assert planner_env.opened == ["https://p99planner.com/import/token1"]

    planner_env.handler.forget_claim()
    assert planner_env.planner.released == ["token1"]
    assert planner_env.handler.claim_url() == ""
    assert planner_env.handler.open_claim() is False


def test_export_filename_matches_what_the_game_writes() -> None:
    dump = build_dump(DUMP, character="Wermule", kind=DumpKind.INVENTORY, captured_at=T0)
    assert dump is not None
    assert export_filename(dump) == "Wermule-Inventory.txt"


# --- the manual path --------------------------------------------------------


def test_manual_upload_ignores_the_session_start_gate(planner_env: Env) -> None:
    """Auto-upload skips old dumps; asking for one explicitly must not."""
    old = planner_env.store("Xantik", DUMP, T0 - timedelta(days=30))
    planner_env.handler.upload_now([old])
    assert len(planner_env.planner.staged) == 1


def test_manual_upload_takes_a_whole_roster_in_one_call(planner_env: Env) -> None:
    dumps = [
        planner_env.store("Xantik", DUMP, T0),
        planner_env.store("Bankmule1", DUMP, T0),
        planner_env.store("Bankmule2", DUMP, T0),
    ]
    planner_env.handler.upload_now(dumps)

    assert len(planner_env.planner.staged) == 1  # one POST, not three
    assert [file.name for file in planner_env.planner.staged[0]] == [
        "Xantik-Inventory.txt",
        "Bankmule1-Inventory.txt",
        "Bankmule2-Inventory.txt",
    ]


def test_manual_upload_reports_why_it_did_nothing(tmp_path: Path) -> None:
    env = Env(tmp_path, target="off")
    dump = env.store("Xantik", DUMP, T0)
    assert "off" in env.handler.upload_now([dump])

    env.state["target"] = "pigparse"
    env.state["token"] = ""
    assert "Log in" in env.handler.upload_now([dump])

    book = build_dump(SPELLBOOK, character="Xantik", kind=DumpKind.SPELLBOOK, captured_at=T0)
    assert book is not None
    assert "No inventory" in env.handler.upload_now([book])


def test_no_api_or_no_server_is_a_no_op(tmp_path: Path) -> None:
    env = Env(tmp_path, api=None)
    env.dump("Xantik-Inventory.txt", DUMP, T0 + timedelta(minutes=1))
    env.watcher.tick(T0 + timedelta(minutes=1))
    assert env.api.uploads == []

    logged_out = Env(tmp_path / "b")
    logged_out.player.reset_for("", None)
    logged_out.dump("Xantik-Inventory.txt", DUMP, T0 + timedelta(minutes=1))
    logged_out.watcher.tick(T0 + timedelta(minutes=1))
    assert logged_out.api.uploads == []
