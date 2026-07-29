"""core.socialsync — capturing in-game macro changes into the local mirror."""

from datetime import datetime, timedelta
from pathlib import Path

from nparseplus.core import socialstore
from nparseplus.core.socials import Social
from nparseplus.core.socialstore import SocialOrigin
from nparseplus.core.socialsync import SCAN_INTERVAL_SECONDS, SocialSyncWatcher

NOW = datetime(2026, 7, 29, 12, 0, 0)
LATER = NOW + timedelta(seconds=SCAN_INTERVAL_SECONDS + 1)

INI = """[Defaults]
Version=1

[Socials]
Page1Button1Name=Assist
Page1Button1Line1=/assist
"""


class Env:
    def __init__(self, tmp_path: Path) -> None:
        self.eq_dir = tmp_path / "eq"
        (self.eq_dir / "uifiles").mkdir(parents=True)
        (self.eq_dir / "eqgame.exe").write_text("")
        self.ini = self.eq_dir / "Xantik_P1999Green.ini"
        self.ini.write_text(INI)
        self.store_dir = tmp_path / "store"
        self.store_dir.mkdir()
        self.enabled = True
        self.running = False

    def watcher(self) -> SocialSyncWatcher:
        return SocialSyncWatcher(
            get_eq_dir=lambda: self.eq_dir,
            get_store_dir=lambda: self.store_dir,
            is_enabled=lambda: self.enabled,
            get_servers=lambda: ["P1999Green"],
            is_running=lambda: self.running,
        )

    def store(self):
        return socialstore.load_store(
            socialstore.store_path(self.store_dir, "Xantik", "P1999Green")
        )

    def touch(self, text: str, *, when: float) -> None:
        self.ini.write_text(text)
        import os

        os.utime(self.ini, (when, when))


def test_sync_creates_a_mirror_from_the_game_files(tmp_path: Path) -> None:
    env = Env(tmp_path)
    assert env.watcher().sync(NOW) == 1

    store = env.store()
    assert store is not None
    assert store.character == "Xantik"
    assert store.origin_at(1, 1) is SocialOrigin.GAME
    assert store.at(1, 1).social.name == "Assist"


def test_sync_never_writes_into_the_eq_directory(tmp_path: Path) -> None:
    env = Env(tmp_path)
    before = env.ini.read_bytes()
    env.watcher().sync(NOW)
    assert env.ini.read_bytes() == before
    assert not (env.eq_dir / "socials_backup").exists()


def test_sync_folds_in_a_macro_made_in_game(tmp_path: Path) -> None:
    env = Env(tmp_path)
    watcher = env.watcher()
    watcher.sync(NOW)

    env.touch(INI + "Page2Button2Name=New\nPage2Button2Line1=/new\n", when=1_000_000)
    assert watcher.sync(LATER) == 1
    assert env.store().at(2, 2).social.name == "New"
    assert watcher.last_added == 1


def test_sync_preserves_provenance_of_macros_we_wrote(tmp_path: Path) -> None:
    env = Env(tmp_path)
    path = socialstore.store_path(env.store_dir, "Xantik", "P1999Green")
    store = socialstore.new_store("Xantik", "P1999Green", now=NOW)
    socialstore.mark_written(
        store,
        [Social(page=1, button=1, name="Assist", lines=["/assist"])],
        origin=SocialOrigin.LOCAL,
        now=NOW,
    )
    socialstore.save_store(path, store)

    env.watcher().sync(NOW)
    assert env.store().origin_at(1, 1) is SocialOrigin.LOCAL


def test_sync_skips_files_whose_mtime_has_not_moved(tmp_path: Path) -> None:
    env = Env(tmp_path)
    watcher = env.watcher()
    assert watcher.sync(NOW) == 1
    assert watcher.sync(LATER) == 0  # nothing changed


def test_sync_is_a_no_op_without_a_valid_eq_install(tmp_path: Path) -> None:
    env = Env(tmp_path)
    (env.eq_dir / "eqgame.exe").unlink()
    assert env.watcher().sync(NOW) == 0
    assert env.store() is None


def test_sync_survives_an_unwritable_store(tmp_path: Path) -> None:
    env = Env(tmp_path)
    env.store_dir = tmp_path / "eq" / "Xantik_P1999Green.ini" / "nope"
    # Reports the file as examined and does not raise, even though the mirror
    # cannot be written.
    assert env.watcher().sync(NOW) == 1


# -- tick behaviour ----------------------------------------------------------


def test_tick_syncs_once_when_the_game_is_already_closed(tmp_path: Path) -> None:
    env = Env(tmp_path)
    watcher = env.watcher()
    watcher.tick(NOW)
    assert env.store() is not None

    # A second tick with nothing changed does no further work.
    env.store_dir.joinpath("marker").write_text("")
    watcher.tick(LATER)
    assert watcher.last_added == 1


def test_tick_syncs_on_the_client_exit_edge(tmp_path: Path) -> None:
    env = Env(tmp_path)
    env.running = True
    watcher = env.watcher()

    watcher.tick(NOW)
    assert env.store() is None  # game up: nothing read

    env.touch(INI + "Page3Button3Name=Made\nPage3Button3Line1=/made\n", when=1_000_000)
    env.running = False
    watcher.tick(LATER)
    assert env.store().at(3, 3).social.name == "Made"


def test_tick_does_nothing_while_the_client_runs(tmp_path: Path) -> None:
    env = Env(tmp_path)
    env.running = True
    watcher = env.watcher()
    watcher.tick(NOW)
    watcher.tick(LATER)
    assert env.store() is None


def test_tick_respects_the_opt_in(tmp_path: Path) -> None:
    env = Env(tmp_path)
    env.enabled = False
    watcher = env.watcher()
    watcher.tick(NOW)
    assert env.store() is None

    # Enabling mid-session syncs on the next tick rather than waiting for a
    # launch/quit cycle.
    env.enabled = True
    watcher.tick(LATER)
    assert env.store() is not None


def test_tick_throttles_between_scans(tmp_path: Path) -> None:
    env = Env(tmp_path)
    watcher = env.watcher()
    watcher.tick(NOW)
    env.touch(INI + "Page4Button4Name=Fast\nPage4Button4Line1=/fast\n", when=1_000_000)
    watcher.tick(NOW + timedelta(seconds=1))  # inside the interval
    assert env.store().at(4, 4) is None


def test_tick_never_raises(tmp_path: Path) -> None:
    def boom() -> Path:
        raise RuntimeError("no eq dir for you")

    watcher = SocialSyncWatcher(
        get_eq_dir=boom,
        get_store_dir=lambda: tmp_path,
        is_enabled=lambda: True,
        is_running=lambda: False,
    )
    watcher.tick(NOW)  # swallowed


def test_status_text_reports_the_last_sync(tmp_path: Path) -> None:
    env = Env(tmp_path)
    watcher = env.watcher()
    assert "Not synced yet" in watcher.status_text()

    watcher.sync(NOW)
    text = watcher.status_text()
    assert "Last synced at 12:00" in text
    assert "1 new" in text
