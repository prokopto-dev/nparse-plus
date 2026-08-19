"""Tests for nparseplus.config.settings persistence and helpers."""

import json
import threading
import time
from pathlib import Path

import pytest

from nparseplus.config.settings import (
    _LEGACY_DEFAULT_REGISTRY_URL,
    BUILTIN_REGISTRY_URL,
    MIN_REGION_HEIGHT,
    MIN_REGION_WIDTH,
    SCHEMA_VERSION,
    DebouncedSaver,
    OverlayRegion,
    PlayerInfo,
    PluginEntry,
    PluginsSettings,
    RegistrySource,
    Settings,
    WindowLayoutPreset,
    WindowState,
    get_player,
    load_settings,
    normalize_registry_url,
    save_settings,
)
from nparseplus.core.triggers.model import Trigger, TriggerTimer


def test_defaults_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    original = Settings()
    save_settings(original, path)
    loaded = load_settings(path)
    assert loaded == original


def test_populated_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    original = Settings()
    original.general.font_size = 14
    original.sharing.mode = "nparse"
    original.windows["maps"] = WindowState(geometry=(10, 20, 300, 400), opacity=0.8, shown=True)
    original.window_layouts["Laptop mode"] = WindowLayoutPreset(
        geometries={"maps": (0, 0, 800, 600), "spells": (800, 0, 220, 600)}
    )
    original.players.append(PlayerInfo(name="Xantik", server="green", level=54))
    original.triggers.append(
        Trigger(
            trigger_name="Journeyman Boots",
            search_text="Your feet feel quick\\.",
            timer=TriggerTimer(timer_name="JBoots", minutes=18),
        )
    )
    save_settings(original, path)
    loaded = load_settings(path)
    assert loaded == original
    assert loaded.windows["maps"].geometry == (10, 20, 300, 400)
    assert loaded.window_layouts["Laptop mode"].geometries["spells"] == (800, 0, 220, 600)
    assert loaded.triggers[0].trigger_id == original.triggers[0].trigger_id


def test_window_state_dict_handling(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    settings = Settings()
    settings.windows["spells"] = WindowState(clickthrough=True, always_on_top=False)
    settings.windows["dps"] = WindowState()  # geometry stays None
    save_settings(settings, path)
    loaded = load_settings(path)
    assert set(loaded.windows) == {"spells", "dps"}
    assert loaded.windows["spells"].clickthrough is True
    assert loaded.windows["spells"].always_on_top is False
    assert loaded.windows["dps"].geometry is None
    # Unknown window keys are the norm: the dict accepts any name.
    loaded.windows["brand_new_overlay"] = WindowState(shown=True)
    save_settings(loaded, path)
    assert load_settings(path).windows["brand_new_overlay"].shown is True


def test_overlay_regions_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    settings = Settings()
    settings.windows["events"] = WindowState(
        overlay_regions={
            "lanes": OverlayRegion(anchor="top", dx=10, dy=-4, width=540),
            "alert": OverlayRegion(anchor="center", dx=-8, dy=20),
            "bars": OverlayRegion(anchor="bottom"),
        }
    )
    save_settings(settings, path)
    loaded = load_settings(path)
    regions = loaded.windows["events"].overlay_regions
    assert regions is not None
    assert regions["lanes"] == OverlayRegion(anchor="top", dx=10, dy=-4, width=540)
    assert regions["alert"].anchor == "center"
    assert regions["bars"].width is None


def test_legacy_settings_without_overlay_regions_load_none(tmp_path: Path) -> None:
    # A settings.json predating the feature has no overlay_regions key; it must
    # load with the field defaulting to None (legacy stacked layout).
    path = tmp_path / "settings.json"
    settings = Settings()
    settings.windows["events"] = WindowState(geometry=(0, 0, 800, 600))
    save_settings(settings, path)
    data = json.loads(path.read_text(encoding="utf-8"))
    del data["windows"]["events"]["overlay_regions"]
    path.write_text(json.dumps(data), encoding="utf-8")
    loaded = load_settings(path)
    assert loaded.windows["events"].overlay_regions is None


def test_load_ignores_removed_spellwindow_keys(tmp_path: Path) -> None:
    # Removed keys: show_trigger_timers (the old single "Custom Timer" section
    # split into Mob/Roll/Custom) and raid_mode_auto (the EQTool global-flag
    # raid regrouping — replaced by the opt-in, per-row raid_group_by_spell in
    # #17). An older settings.json still carrying them must load without error
    # (extras are ignored) and the new toggles fall back to their defaults.
    path = tmp_path / "settings.json"
    save_settings(Settings(), path)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["spellwindow"]["show_trigger_timers"] = False
    data["spellwindow"]["raid_mode_auto"] = True
    path.write_text(json.dumps(data), encoding="utf-8")
    loaded = load_settings(path)
    assert loaded.spellwindow.show_mob_timers is True
    assert loaded.spellwindow.show_roll_timers is True
    assert loaded.spellwindow.show_custom_timers is True
    assert not hasattr(loaded.spellwindow, "show_trigger_timers")
    assert not hasattr(loaded.spellwindow, "raid_mode_auto")
    # The redesigned raid mode is a NEW, distinct key that defaults off.
    assert loaded.spellwindow.raid_group_by_spell is False


def test_new_1_11_optin_defaults_and_roundtrip(tmp_path: Path) -> None:
    """The 1.11-batch opt-ins default off and survive a save/load round-trip."""
    s = Settings()
    assert s.general.ch_cadence_indicator is False
    assert s.general.ch_cadence_patterns  # defaults to the stock cadence regexes
    assert s.spellwindow.raid_group_by_spell is False
    assert s.spellwindow.post_expiry_flash_enabled is False
    assert s.spellwindow.post_expiry_flash_seconds == 30
    assert s.spellwindow.post_expiry_flash_spells == []

    s.general.ch_cadence_indicator = True
    s.general.ch_cadence_patterns = [r"cadence (\d+)"]
    s.spellwindow.raid_group_by_spell = True
    s.spellwindow.post_expiry_flash_enabled = True
    s.spellwindow.post_expiry_flash_seconds = 45
    s.spellwindow.post_expiry_flash_spells = ["Clarity", "Aegolism"]
    path = tmp_path / "settings.json"
    save_settings(s, path)
    loaded = load_settings(path)
    assert loaded.general.ch_cadence_indicator is True
    assert loaded.general.ch_cadence_patterns == [r"cadence (\d+)"]
    assert loaded.spellwindow.raid_group_by_spell is True
    assert loaded.spellwindow.post_expiry_flash_enabled is True
    assert loaded.spellwindow.post_expiry_flash_seconds == 45
    assert loaded.spellwindow.post_expiry_flash_spells == ["Clarity", "Aegolism"]


def test_atomic_write_leaves_valid_json_and_no_tmp(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    save_settings(Settings(), path)
    settings = Settings()
    settings.maps.last_zone = "gfaydark"
    save_settings(settings, path)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["maps"]["last_zone"] == "gfaydark"
    assert data["schema_version"] == 1
    leftovers = [p.name for p in tmp_path.iterdir() if p.name != "settings.json"]
    assert leftovers == []


def test_load_missing_file_returns_defaults(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)  # ensure no stray legacy config in CWD is picked up
    loaded = load_settings(tmp_path / "does-not-exist.json")
    assert loaded == Settings()


def test_load_corrupt_file_returns_defaults(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text("{not json", encoding="utf-8")
    assert load_settings(path) == Settings()


def test_get_player_find_or_create() -> None:
    settings = Settings()
    first = get_player(settings, "Xantik", "green")
    assert settings.players == [first]
    again = get_player(settings, "Xantik", "green")
    assert again is first
    other_server = get_player(settings, "Xantik", "blue")
    assert other_server is not first
    assert len(settings.players) == 2


def test_debounced_saver_coalesces_bursts() -> None:
    calls: list[float] = []
    done = threading.Event()

    def record() -> None:
        calls.append(time.monotonic())
        done.set()

    saver = DebouncedSaver(record, delay=0.05)
    for _ in range(10):
        saver.request_save()
    assert calls == []  # nothing until the delay elapses
    assert done.wait(timeout=2.0)
    time.sleep(0.1)  # allow any (incorrect) extra timers to fire
    assert len(calls) == 1


def test_debounced_saver_flush_runs_pending_save_immediately() -> None:
    calls: list[int] = []
    saver = DebouncedSaver(lambda: calls.append(1), delay=60.0)
    saver.request_save()
    saver.flush()
    assert calls == [1]
    saver.flush()  # nothing pending: no double save
    assert calls == [1]


def test_debounced_saver_cancel_discards_pending_save() -> None:
    calls: list[int] = []
    saver = DebouncedSaver(lambda: calls.append(1), delay=0.05)
    saver.request_save()
    saver.cancel()
    time.sleep(0.15)
    assert calls == []


def test_debounced_saver_thread_safety() -> None:
    calls: list[int] = []
    saver = DebouncedSaver(lambda: calls.append(1), delay=0.02)

    def hammer() -> None:
        for _ in range(50):
            saver.request_save()

    threads = [threading.Thread(target=hammer) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    saver.flush()
    time.sleep(0.1)
    # 400 requests must coalesce into a handful of saves at most (usually 1).
    assert 1 <= len(calls) <= 5


def test_plugins_entries_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    original = Settings()
    original.plugins.entries["merchant-prices"] = PluginEntry(
        enabled=True, approved=True, last_version="1.2.0"
    )
    original.plugins.entries["dkp"] = PluginEntry(
        enabled=False,
        approved=True,
        source_url="https://example.com/dkp.zip",
        sha256="f" * 64,
    )
    save_settings(original, path)
    loaded = load_settings(path)
    assert loaded.plugins.entries == original.plugins.entries
    assert loaded.plugins.entries["dkp"].enabled is False
    assert loaded.plugins.entries["dkp"].sha256 == "f" * 64


def test_plugins_enabled_roundtrips_and_defaults_off(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    assert Settings().plugins.enabled is False

    original = Settings()
    original.plugins.enabled = True
    save_settings(original, path)
    assert load_settings(path).plugins.enabled is True


def test_plugin_entry_update_url_roundtrips(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    assert PluginEntry().update_url == ""

    original = Settings()
    original.plugins.entries["selfhosted"] = PluginEntry(
        approved=True, update_url="https://you.example/p/index.json"
    )
    save_settings(original, path)
    loaded = load_settings(path)
    assert loaded.plugins.entries["selfhosted"].update_url == "https://you.example/p/index.json"


def test_plugins_update_check_defaults_on_and_roundtrips(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    # Matches general.update_check: the app checks for its own updates by
    # default, and this is the same promise for add-ons.
    assert Settings().plugins.update_check is True

    original = Settings()
    original.plugins.update_check = False
    save_settings(original, path)
    assert load_settings(path).plugins.update_check is False


def test_settings_written_by_a_newer_build_still_load(tmp_path: Path) -> None:
    # Downgrading drops the unknown block rather than refusing to start; the
    # consequence is that plugin consent is re-asked, which is the safe way to
    # lose it.
    path = tmp_path / "settings.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": SCHEMA_VERSION,
                "plugins": {"enabled": True, "entries": {}, "some_future_key": 1},
                "a_whole_future_section": {"nested": True},
            }
        )
    )
    loaded = load_settings(path)
    assert loaded.plugins.enabled is True


class TestRegistrySettings:
    def test_registries_default_empty_and_the_default_is_enabled(self) -> None:
        plugins = Settings().plugins
        assert plugins.registries == []
        assert plugins.default_registry_enabled is True

    def test_registries_roundtrip_in_order(self, tmp_path: Path) -> None:
        path = tmp_path / "settings.json"
        original = Settings()
        original.plugins.registries = [
            RegistrySource(url="https://a.example/index.json", name="A"),
            RegistrySource(url="https://b.example/index.json", name="B", enabled=False),
        ]
        original.plugins.default_registry_enabled = False
        save_settings(original, path)

        loaded = load_settings(path)
        assert [r.url for r in loaded.plugins.registries] == [
            "https://a.example/index.json",
            "https://b.example/index.json",
        ]
        assert loaded.plugins.registries[1].enabled is False
        assert loaded.plugins.default_registry_enabled is False

    def test_legacy_registry_url_folds_in_and_clears(self, tmp_path: Path) -> None:
        path = tmp_path / "settings.json"
        path.write_text(json.dumps({"plugins": {"registry_url": "https://old.example/index.json"}}))
        loaded = load_settings(path)
        assert [r.url for r in loaded.plugins.registries] == ["https://old.example/index.json"]
        assert loaded.plugins.registry_url == ""

        # Idempotent: saving and reloading must not add a second copy.
        save_settings(loaded, path)
        assert len(load_settings(path).plugins.registries) == 1

    def test_legacy_registry_url_already_present_is_not_duplicated(self) -> None:
        plugins = PluginsSettings(
            registry_url="https://a.example/index.json",
            registries=[RegistrySource(url="https://A.example/index.json")],
        )
        assert [r.url for r in plugins.registries] == ["https://a.example/index.json"]

    def test_unusable_entries_are_dropped_not_fatal(self, tmp_path: Path) -> None:
        # A raise here would make load_settings discard the whole document.
        path = tmp_path / "settings.json"
        path.write_text(
            json.dumps(
                {
                    "general": {"eq_log_dir": "/keep/me"},
                    "plugins": {
                        "registry_url": "http://insecure.example/index.json",
                        "registries": [
                            {"url": "https://good.example/index.json"},
                            {"url": "ftp://nope.example/index.json"},
                            {"url": "   "},
                            {"url": "https://good.example/index.json", "name": "dupe"},
                        ],
                    },
                }
            )
        )
        loaded = load_settings(path)
        assert [r.url for r in loaded.plugins.registries] == ["https://good.example/index.json"]
        assert loaded.plugins.registries[0].name == ""  # first wins, not the dupe
        # The document survived. Compare as Path — the separator is
        # platform-dependent and this assertion is about the value, not spelling.
        assert loaded.general.eq_log_dir == Path("/keep/me")

    def test_plugin_entry_registry_url_roundtrips(self, tmp_path: Path) -> None:
        path = tmp_path / "settings.json"
        original = Settings()
        original.plugins.entries["demo"] = PluginEntry(
            registry_url="https://a.example/index.json", sha256="a" * 64
        )
        save_settings(original, path)
        entry = load_settings(path).plugins.entries["demo"]
        assert entry.registry_url == "https://a.example/index.json"


class TestBuiltInRegistryMoved:
    """The one-shot migration for #130 — the built-in registry's new home.

    A plugin installed before the move records the old URL, which after the
    move names no configured registry at all: the manager reads that as a
    third-party source and every offer from the new default becomes a
    cross-source confirmation for what is the same catalogue. A stored
    *registry* row carrying that URL is the same fossil seen from the other
    side — inert while it duplicated the built-in row, an uninvited
    third-party registry the moment it stops.

    It runs once per document, because after the move the same URL is an
    ordinary registry somebody may deliberately add.
    """

    def test_an_install_from_the_old_default_follows_it(self, tmp_path: Path) -> None:
        path = tmp_path / "settings.json"
        path.write_text(
            json.dumps(
                {
                    "plugins": {
                        "entries": {
                            "merchant-mode": {
                                "approved": True,
                                "registry_url": _LEGACY_DEFAULT_REGISTRY_URL,
                                "sha256": "a" * 64,
                            }
                        }
                    }
                }
            )
        )
        entry = load_settings(path).plugins.entries["merchant-mode"]
        assert entry.registry_url == BUILTIN_REGISTRY_URL
        # Consent and the recorded bytes are untouched: this repoints a
        # record, it does not re-take a decision.
        assert entry.approved is True
        assert entry.sha256 == "a" * 64

    def test_case_and_whitespace_spellings_still_match(self) -> None:
        plugins = PluginsSettings(
            entries={
                "demo": PluginEntry(
                    registry_url="  HTTPS://Prokopto-Dev.GitHub.IO/nparseplus-plugins/index.json  "
                )
            }
        )
        assert plugins.entries["demo"].registry_url == BUILTIN_REGISTRY_URL

    def test_a_stored_copy_of_the_old_default_is_dropped_not_promoted(self) -> None:
        """It was hidden behind the row it duplicated; it must not surface now.

        ``resolve_registries`` collapsed a stored copy of the then-default
        into the built-in row and ``PluginHost.add_registry`` refused to
        create one, so this row was never visible and never separately
        fetched. Kept, it would un-collapse into a third-party registry the
        user never added — *and* it would make the install below look like it
        came from somewhere other than the built-in catalogue.
        """
        plugins = PluginsSettings(
            registries=[
                RegistrySource(url=_LEGACY_DEFAULT_REGISTRY_URL, name="Mine"),
                RegistrySource(url="https://guild.example/index.json"),
            ],
            entries={"demo": PluginEntry(registry_url=_LEGACY_DEFAULT_REGISTRY_URL)},
        )
        assert [r.url for r in plugins.registries] == ["https://guild.example/index.json"]
        assert plugins.entries["demo"].registry_url == BUILTIN_REGISTRY_URL

    def test_a_legacy_override_of_the_old_url_leaves_no_row_behind(self) -> None:
        """The deprecated single-registry override folds in, then drops out.

        This is the population the guard has to reach: someone who set
        ``plugins.registry_url`` to the then-default on <=1.18 had it folded
        into ``registries`` by a later build, invisibly.
        """
        plugins = PluginsSettings(
            registry_url=_LEGACY_DEFAULT_REGISTRY_URL,
            entries={"demo": PluginEntry(registry_url=_LEGACY_DEFAULT_REGISTRY_URL)},
        )
        assert plugins.registries == []
        assert plugins.registry_url == ""
        assert plugins.entries["demo"].registry_url == BUILTIN_REGISTRY_URL

    def test_a_registry_added_after_the_move_is_left_alone(self) -> None:
        """Post-move the old URL is an ordinary registry — and stays one.

        Once the migration has run, adding that index is a trust decision
        like any other: the row survives every reload, and what it vouched
        for keeps naming it.
        """
        plugins = PluginsSettings(
            registry_move_applied=True,
            registries=[RegistrySource(url=_LEGACY_DEFAULT_REGISTRY_URL, name="Mine")],
            entries={"demo": PluginEntry(registry_url=_LEGACY_DEFAULT_REGISTRY_URL)},
        )
        assert [r.url for r in plugins.registries] == [_LEGACY_DEFAULT_REGISTRY_URL]
        assert plugins.entries["demo"].registry_url == _LEGACY_DEFAULT_REGISTRY_URL

    def test_a_fresh_document_records_the_move_as_done(self) -> None:
        assert Settings().plugins.registry_move_applied is True

    def test_other_provenance_is_never_rewritten(self) -> None:
        plugins = PluginsSettings(
            entries={
                "sideloaded": PluginEntry(),
                "elsewhere": PluginEntry(registry_url="https://other.example/index.json"),
                "fork": PluginEntry(
                    registry_url="https://someone-else.github.io/nparseplus-plugins/index.json"
                ),
                "querystring": PluginEntry(registry_url=_LEGACY_DEFAULT_REGISTRY_URL + "?ref=x"),
            }
        )
        assert plugins.entries["sideloaded"].registry_url == ""
        assert plugins.entries["elsewhere"].registry_url == "https://other.example/index.json"
        assert (
            plugins.entries["fork"].registry_url
            == "https://someone-else.github.io/nparseplus-plugins/index.json"
        )
        assert plugins.entries["querystring"].registry_url.endswith("?ref=x")

    def test_a_malformed_provenance_record_is_survivable(self, tmp_path: Path) -> None:
        """A raise here would cost the user every other setting they have."""
        path = tmp_path / "settings.json"
        path.write_text(
            json.dumps(
                {
                    "general": {"eq_log_dir": "/keep/me"},
                    "plugins": {"entries": {"demo": {"registry_url": "not a url at all"}}},
                }
            )
        )
        loaded = load_settings(path)
        assert loaded.plugins.entries["demo"].registry_url == "not a url at all"
        assert loaded.general.eq_log_dir == Path("/keep/me")

    def test_the_marker_persists_so_the_migration_runs_once(self, tmp_path: Path) -> None:
        path = tmp_path / "settings.json"
        path.write_text(
            json.dumps(
                {
                    "plugins": {
                        "registries": [{"url": _LEGACY_DEFAULT_REGISTRY_URL}],
                        "entries": {"demo": {"registry_url": _LEGACY_DEFAULT_REGISTRY_URL}},
                    }
                }
            )
        )
        once = load_settings(path)
        assert once.plugins.registry_move_applied is True
        assert once.plugins.registries == []
        assert once.plugins.entries["demo"].registry_url == BUILTIN_REGISTRY_URL

        # Saved and reloaded, the user may now add that index deliberately.
        save_settings(once, path)
        reloaded = load_settings(path)
        reloaded.plugins.registries = [RegistrySource(url=_LEGACY_DEFAULT_REGISTRY_URL)]
        save_settings(reloaded, path)
        assert [r.url for r in load_settings(path).plugins.registries] == [
            _LEGACY_DEFAULT_REGISTRY_URL
        ]


class TestNormalizeRegistryUrl:
    def test_lowercases_scheme_and_host_but_not_the_path(self) -> None:
        assert (
            normalize_registry_url("  HTTPS://Example.COM/Path/Index.json  ")
            == "https://example.com/Path/Index.json"
        )

    @pytest.mark.parametrize(
        "url",
        ["", "   ", "http://x.example/i.json", "ftp://x/i.json", "x.example/i.json", "https://"],
    )
    def test_rejects_unusable(self, url: str) -> None:
        with pytest.raises(ValueError):
            normalize_registry_url(url)


class TestDpsSettings:
    """The DPS meter's counting rules and the melee_only migration (#80/#81)."""

    def test_defaults(self) -> None:
        dps = Settings().dps
        assert dps.damage_sources == "melee+mine"
        # Opt-in: how to count a pet is an opinion, not a defect to fix on
        # the user's behalf, so it must not change on upgrade.
        assert dps.count_pet_damage is False
        assert dps.spell_credit_window_seconds == 2.0
        # The legacy bool is gone from a fresh document.
        assert dps.melee_only is None

    def test_legacy_melee_only_true_folds_in_and_clears(self, tmp_path: Path) -> None:
        """Upgrading never changes what someone's number means.

        The mapping is literal even though ``melee+mine`` is the fresh-install
        default and arguably carries the old intent: a meter must not start
        counting differently because the user updated the app.
        """
        path = tmp_path / "settings.json"
        path.write_text(json.dumps({"dps": {"melee_only": True}}))
        loaded = load_settings(path)
        assert loaded.dps.damage_sources == "melee"
        assert loaded.dps.melee_only is None

        # Idempotent: saving and reloading must not move it again.
        save_settings(loaded, path)
        assert load_settings(path).dps.damage_sources == "melee"

    def test_a_fresh_install_gets_the_caster_default(self) -> None:
        # The split from the migration above is the point: a new user gets
        # the mode that actually works for a caster.
        assert Settings().dps.damage_sources == "melee+mine"

    def test_legacy_melee_only_false_asked_for_everything(self, tmp_path: Path) -> None:
        path = tmp_path / "settings.json"
        path.write_text(json.dumps({"dps": {"melee_only": False}}))
        loaded = load_settings(path)
        assert loaded.dps.damage_sources == "all"
        assert loaded.dps.melee_only is None

    def test_an_explicit_mode_wins_over_the_legacy_bool(self) -> None:
        # A document written by this version must not be re-migrated. Keyed
        # on whether the field was actually written, not on whether it still
        # equals the default, so an explicit "melee+mine" survives a stale
        # melee_only: true sitting beside it.
        dps = Settings(dps={"melee_only": False, "damage_sources": "melee"}).dps
        assert dps.damage_sources == "melee"
        assert dps.melee_only is None

        kept = Settings(dps={"melee_only": True, "damage_sources": "melee+mine"}).dps
        assert kept.damage_sources == "melee+mine"

    def test_the_rest_of_the_document_survives_a_migration(self, tmp_path: Path) -> None:
        path = tmp_path / "settings.json"
        path.write_text(
            json.dumps(
                {
                    "general": {"eq_log_dir": "/keep/me"},
                    "dps": {"melee_only": True, "trailing_window_seconds": 6.0},
                }
            )
        )
        loaded = load_settings(path)
        assert loaded.general.eq_log_dir == Path("/keep/me")
        assert loaded.dps.trailing_window_seconds == 6.0


class TestOverlayRegionSizeBounds:
    """#107. A region small enough to hold nothing renders nothing, silently:
    the Alerts region reads its height as an exact text budget, so a legacy or
    hand-edited value could leave the headline a couple of pixels tall with no
    error anywhere. The bounds are enforced here, where the file is read —
    Qt-free, because ``config`` may not import PySide6."""

    def test_a_too_small_size_is_clamped_up_not_rejected(self) -> None:
        region = OverlayRegion(anchor="center", width=10, height=8)
        assert region.width == MIN_REGION_WIDTH
        assert region.height == MIN_REGION_HEIGHT

    def test_a_usable_size_is_left_exactly_alone(self) -> None:
        region = OverlayRegion(anchor="center", width=600, height=140)
        assert (region.width, region.height) == (600, 140)

    def test_none_still_means_use_the_default(self) -> None:
        region = OverlayRegion(anchor="center")
        assert region.width is None
        assert region.height is None

    @pytest.mark.parametrize("junk", ["wide", [], {}, True, False])
    def test_a_size_that_is_not_a_number_drops_to_the_default(self, junk) -> None:
        """Including bools: ``int(True)`` is 1, which would clamp to the floor
        and quietly invent a size the file never asked for."""
        region = OverlayRegion.model_validate({"anchor": "center", "height": junk})
        assert region.height is None

    def test_a_bad_size_never_costs_the_rest_of_the_document(self, tmp_path: Path) -> None:
        """The house rule, and the reason this is a validator rather than only
        the ``ge=`` bounds: ``load_settings`` reads a ValueError as a corrupt
        document and falls back to defaults, so one bad pixel count would
        discard everything else configured (the PluginsSettings precedent)."""
        path = tmp_path / "settings.json"
        path.write_text(
            json.dumps(
                {
                    "general": {"eq_log_dir": "/keep/me"},
                    "windows": {
                        "events": {
                            "overlay_regions": {
                                "alert": {"anchor": "center", "width": 0, "height": -40},
                            }
                        }
                    },
                }
            )
        )
        loaded = load_settings(path)

        assert loaded.general.eq_log_dir == Path("/keep/me")
        region = loaded.windows["events"].overlay_regions["alert"]
        assert region.width == MIN_REGION_WIDTH
        assert region.height == MIN_REGION_HEIGHT

    def test_the_repaired_value_survives_a_round_trip(self, tmp_path: Path) -> None:
        path = tmp_path / "settings.json"
        settings = Settings()
        settings.windows["events"] = WindowState(
            overlay_regions={"alert": OverlayRegion(anchor="center", height=4)}
        )
        save_settings(settings, path)

        region = load_settings(path).windows["events"].overlay_regions["alert"]
        assert region.height == MIN_REGION_HEIGHT
