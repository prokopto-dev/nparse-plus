"""PluginHost lifecycle: classification, consent, activation, isolation."""

from __future__ import annotations

from pathlib import Path

import pytest

from nparseplus.config.settings import Settings

from .conftest import APP_VERSION, approve, write_plugin


def statuses_by_id(host) -> dict[str, str]:
    return {p.plugin_id or p.source.name: p.status for p in host.statuses()}


def test_unknown_plugin_is_pending_consent(make_host, plugins_dir: Path) -> None:
    write_plugin(plugins_dir, "newbie.py", plugin_id="newbie")
    host = make_host()
    host.discover_and_load()
    assert statuses_by_id(host) == {"newbie": "pending_consent"}
    host.activate_enabled()
    assert statuses_by_id(host) == {"newbie": "pending_consent"}  # never activated


def test_consent_accept_then_activate(make_host, plugins_dir: Path, settings: Settings) -> None:
    write_plugin(plugins_dir, "good.py", plugin_id="good")
    host = make_host()
    host.discover_and_load()
    host.record_consent("good", True)
    entry = settings.plugins.entries["good"]
    assert entry.approved and entry.enabled and entry.last_version == "1.0.0"
    host.activate_enabled()
    assert statuses_by_id(host) == {"good": "active"}


def test_consent_decline_persists_disabled(
    make_host, plugins_dir: Path, settings: Settings
) -> None:
    write_plugin(plugins_dir, "nope.py", plugin_id="nope")
    host = make_host()
    host.discover_and_load()
    host.record_consent("nope", False)
    entry = settings.plugins.entries["nope"]
    assert entry.approved and not entry.enabled
    host.activate_enabled()
    assert statuses_by_id(host) == {"nope": "disabled"}

    # A later run must classify straight to disabled, no re-prompt.
    host2 = make_host()
    host2.discover_and_load()
    assert statuses_by_id(host2) == {"nope": "disabled"}
    assert host2.pending_consent() == []


def test_disabled_plugin_not_activated(make_host, plugins_dir: Path, settings: Settings) -> None:
    write_plugin(plugins_dir, "off.py", plugin_id="off")
    approve(settings, "off", enabled=False)
    host = make_host()
    host.discover_and_load()
    host.activate_enabled()
    assert statuses_by_id(host) == {"off": "disabled"}


def test_incompatible_sdk_range(make_host, plugins_dir: Path, settings: Settings) -> None:
    write_plugin(
        plugins_dir,
        "future.py",
        plugin_id="future",
        extra_meta=', requires_sdk=">=99.0"',
    )
    approve(settings, "future")
    host = make_host()
    host.discover_and_load()
    (loaded,) = host.statuses()
    assert loaded.status == "incompatible"
    assert loaded.error is not None and ">=99.0" in loaded.error


def test_min_app_version_gate(make_host, plugins_dir: Path, settings: Settings) -> None:
    write_plugin(
        plugins_dir,
        "demanding.py",
        plugin_id="demanding",
        extra_meta=', min_app_version="99.0.0"',
    )
    approve(settings, "demanding")
    host = make_host()
    host.discover_and_load()
    (loaded,) = host.statuses()
    assert loaded.status == "incompatible"
    assert loaded.error is not None and APP_VERSION in loaded.error


def test_bad_version_string_is_incompatible_not_crash(
    make_host, plugins_dir: Path, settings: Settings
) -> None:
    write_plugin(
        plugins_dir,
        "weird.py",
        plugin_id="weird",
        extra_meta=', requires_sdk="!!nonsense!!"',
    )
    approve(settings, "weird")
    host = make_host()
    host.discover_and_load()
    (loaded,) = host.statuses()
    assert loaded.status == "incompatible"


def test_import_error_is_isolated(make_host, plugins_dir: Path, settings: Settings) -> None:
    plugins_dir.mkdir(parents=True)
    (plugins_dir / "broken.py").write_text("import nothing_here_at_all\n", encoding="utf-8")
    write_plugin(plugins_dir, "fine.py", plugin_id="fine")
    approve(settings, "fine")
    host = make_host()
    host.discover_and_load()
    host.activate_enabled()
    by_id = statuses_by_id(host)
    assert by_id["broken"] == "error"
    assert by_id["fine"] == "active"


def test_activate_raise_marks_error_and_unwinds(
    make_host, plugins_dir: Path, settings: Settings, backend
) -> None:
    ticks_before = len(backend.driver.on_tick)
    parsers_before = len(backend.pipeline._parsers)
    write_plugin(
        plugins_dir,
        "half.py",
        plugin_id="half",
        activate_body=(
            "        ctx.add_tick(lambda now: None)\n"
            "        class P:\n"
            "            def handle(self, line, pctx):\n"
            "                return False\n"
            "        ctx.add_parser(P())\n"
            "        raise RuntimeError('mid-activate boom')"
        ),
    )
    approve(settings, "half")
    host = make_host()
    host.discover_and_load()
    host.activate_enabled()
    (loaded,) = host.statuses()
    assert loaded.status == "error"
    assert loaded.error is not None and "mid-activate" in loaded.error
    assert len(backend.driver.on_tick) == ticks_before
    assert len(backend.pipeline._parsers) == parsers_before
    assert host.window_specs() == []


def test_dropped_tick_is_reported_on_the_host_record(
    make_host, plugins_dir: Path, settings: Settings, backend, monkeypatch
) -> None:
    """The manager page reads this to tell the user their plugin misbehaved."""
    from datetime import datetime

    from nparseplus.core import driver as driver_module

    monkeypatch.setattr(driver_module, "TICK_BUDGET_S", 0.0)  # every run breaches
    write_plugin(
        plugins_dir,
        "hog.py",
        plugin_id="hog",
        activate_body="        ctx.add_tick(lambda now: None)",
    )
    approve(settings, "hog")
    host = make_host()
    host.discover_and_load()
    host.activate_enabled()
    (loaded,) = host.statuses()
    assert loaded.tick_dropped is None

    for _ in range(driver_module.TICK_BREACH_LIMIT):
        backend.driver._run_supervised_ticks(datetime.now())

    assert loaded.status == "active"  # the plugin lives on; only its tick went
    assert loaded.tick_dropped is not None and "removed" in loaded.tick_dropped


def test_tick_dropped_is_none_without_a_context(make_host, plugins_dir: Path) -> None:
    write_plugin(plugins_dir, "quiet.py", plugin_id="quiet")
    host = make_host()
    host.discover_and_load()
    (loaded,) = host.statuses()  # pending_consent: never activated, no context
    assert loaded.tick_dropped is None


def test_duplicate_id_first_wins(make_host, plugins_dir: Path, settings: Settings) -> None:
    write_plugin(plugins_dir, "aaa.py", plugin_id="twin")
    write_plugin(plugins_dir, "bbb.py", plugin_id="twin")
    approve(settings, "twin")
    host = make_host()
    host.discover_and_load()
    host.activate_enabled()
    states = sorted(p.status for p in host.statuses())
    assert states == ["active", "duplicate"]
    active = next(p for p in host.statuses() if p.status == "active")
    assert active.source.name == "aaa"


def test_version_change_updates_entry(make_host, plugins_dir: Path, settings: Settings) -> None:
    write_plugin(plugins_dir, "vers.py", plugin_id="vers", version="2.0.0")
    approve(settings, "vers")
    settings.plugins.entries["vers"].last_version = "1.0.0"
    saves: list[None] = []
    host = make_host(request_save=lambda: saves.append(None))
    host.discover_and_load()
    assert settings.plugins.entries["vers"].last_version == "2.0.0"
    assert saves


def test_shutdown_deactivates_active_only_and_swallows_raise(
    make_host, plugins_dir: Path, settings: Settings, tmp_path: Path
) -> None:
    marker = tmp_path / "deactivated.txt"
    write_plugin(
        plugins_dir,
        "polite.py",
        plugin_id="polite",
        deactivate_body=f"        open({str(marker)!r}, 'w').write('bye')",
    )
    write_plugin(
        plugins_dir,
        "rude.py",
        plugin_id="rude",
        deactivate_body="        raise RuntimeError('rude bye')",
    )
    write_plugin(plugins_dir, "unseen.py", plugin_id="unseen")  # stays pending_consent
    approve(settings, "polite")
    approve(settings, "rude")
    host = make_host()
    host.discover_and_load()
    host.activate_enabled()
    host.shutdown()  # must not raise
    assert marker.read_text() == "bye"


def test_shutdown_unwinds_registrations(
    make_host, plugins_dir: Path, settings: Settings, backend
) -> None:
    from nparseplus.core.events import LineEvent

    ticks_before = list(backend.driver.on_tick)
    parsers_before = list(backend.pipeline._parsers)
    subscribers_before = len(backend.bus._subscribers[LineEvent])
    write_plugin(
        plugins_dir,
        "wired.py",
        plugin_id="wired",
        activate_body=(
            "        from nparseplus.core.events import LineEvent\n"
            "        ctx.subscribe(LineEvent, lambda e: None)\n"
            "        ctx.add_tick(lambda now: None)\n"
            "        class P:\n"
            "            def handle(self, line, pctx):\n"
            "                return False\n"
            "        ctx.add_parser(P())"
        ),
    )
    approve(settings, "wired")
    host = make_host()
    host.discover_and_load()
    host.activate_enabled()
    assert len(backend.bus._subscribers[LineEvent]) == subscribers_before + 1
    assert len(backend.driver.on_tick) == len(ticks_before) + 1
    assert len(backend.pipeline._parsers) == len(parsers_before) + 1

    host.shutdown()
    assert len(backend.bus._subscribers[LineEvent]) == subscribers_before
    assert backend.driver.on_tick == ticks_before
    assert backend.pipeline._parsers == parsers_before


def test_forget_drops_consent_and_trashes_plugin_data(
    make_host, plugins_dir: Path, settings: Settings, tmp_path: Path
) -> None:
    data_root = tmp_path / "plugin-data"
    (data_root / "haunt").mkdir(parents=True)
    (data_root / "haunt" / "storage.json").write_text('{"secret": 1}', encoding="utf-8")
    approve(settings, "haunt")
    saves: list[None] = []
    host = make_host(
        request_save=lambda: saves.append(None),
        plugin_data_dir_override=lambda pid: data_root / pid,
    )
    host.forget("haunt")
    assert "haunt" not in settings.plugins.entries
    assert saves
    assert not (data_root / "haunt").exists()
    assert (plugins_dir / "trash" / "plugin-data" / "haunt" / "storage.json").is_file()


def test_reinstall_under_a_forgotten_id_asks_for_consent_again(
    make_host, plugins_dir: Path, settings: Settings, tmp_path: Path
) -> None:
    """The uninstall/reinstall consent bypass: same id, different plugin."""
    from nparseplus.core.plugins.install import uninstall
    from nparseplus.core.plugins.storage import JsonPluginStorage

    data_root = tmp_path / "plugin-data"
    data_dir = lambda pid: data_root / pid  # noqa: E731 - one-line test stub
    write_plugin(plugins_dir, "impostor.py", plugin_id="impostor")
    host = make_host(plugin_data_dir_override=data_dir)
    host.discover_and_load()
    host.record_consent("impostor", True)
    host.activate_enabled()
    JsonPluginStorage(data_dir("impostor")).save({"api_key": "hunter2"})

    assert uninstall(plugins_dir / "impostor.py", plugins_dir) is None
    host.forget("impostor")

    # Something else now claims the id — it must not inherit the approval...
    write_plugin(plugins_dir, "impostor.py", plugin_id="impostor", version="9.9.9")
    host2 = make_host(plugin_data_dir_override=data_dir)
    host2.discover_and_load()
    assert statuses_by_id(host2) == {"impostor": "pending_consent"}
    host2.activate_enabled()
    assert statuses_by_id(host2) == {"impostor": "pending_consent"}
    # ...nor the previous plugin's private data.
    assert JsonPluginStorage(data_dir("impostor")).load() == {}


def test_set_enabled_without_an_entry_leaves_consent_pending(make_host, settings: Settings) -> None:
    host = make_host()
    host.set_enabled("stranger", True)
    entry = settings.plugins.entries["stranger"]
    assert entry.enabled is True
    assert entry.approved is False  # a checkbox is not consent


def test_set_enabled_toggles_entry(make_host, plugins_dir: Path, settings: Settings) -> None:
    write_plugin(plugins_dir, "flip.py", plugin_id="flip")
    approve(settings, "flip")
    host = make_host()
    host.discover_and_load()
    host.set_enabled("flip", False)
    assert settings.plugins.entries["flip"].enabled is False
    host.set_enabled("flip", True)
    assert settings.plugins.entries["flip"].enabled is True


def test_record_install_upserts_provenance(
    make_host, plugins_dir: Path, settings: Settings
) -> None:
    from nparseplus.core.plugins.install import InstallResult
    from nparseplus_sdk import PluginMeta

    host = make_host()
    result = InstallResult(
        ok=True,
        meta=PluginMeta(id="fresh", name="Fresh", version="2.0.0"),
        sha256="e" * 64,
        source_url="https://example.com/fresh.zip",
    )
    host.record_install(result)
    entry = settings.plugins.entries["fresh"]
    assert entry.approved is False  # consent still due on next launch
    assert entry.last_version == "2.0.0"
    assert entry.sha256 == "e" * 64
    assert entry.source_url == "https://example.com/fresh.zip"

    # Re-install of a known plugin keeps the consent answers.
    approve(settings, "fresh")
    settings.plugins.entries["fresh"].enabled = False
    host.record_install(result)
    entry = settings.plugins.entries["fresh"]
    assert entry.approved is True and entry.enabled is False


def test_record_install_ignores_failures(make_host, settings: Settings) -> None:
    from nparseplus.core.plugins.install import InstallResult

    host = make_host()
    host.record_install(InstallResult(ok=False, errors=["nope"]))
    assert settings.plugins.entries == {}


class TestRegistries:
    def test_the_default_is_present_and_first(self, make_host) -> None:
        from nparseplus.core.plugins.registry import DEFAULT_REGISTRY_URL

        registries = make_host().registries()
        assert registries[0].url == DEFAULT_REGISTRY_URL
        assert registries[0].is_default is True

    def test_add_persists_and_normalizes(self, make_host, settings: Settings) -> None:
        host = make_host()
        assert host.add_registry("  HTTPS://Guild.Example/index.json ", "Guild") is None
        assert [(s.url, s.name) for s in settings.plugins.registries] == [
            ("https://guild.example/index.json", "Guild")
        ]

    @pytest.mark.parametrize(
        ("url", "fragment"),
        [
            ("http://guild.example/i.json", "https"),
            ("   ", "empty"),
        ],
    )
    def test_add_rejects_unusable_urls(self, make_host, url: str, fragment: str) -> None:
        error = make_host().add_registry(url)
        assert error is not None and fragment in error

    def test_add_rejects_duplicates_and_the_default(self, make_host) -> None:
        from nparseplus.core.plugins.registry import DEFAULT_REGISTRY_URL

        host = make_host()
        host.add_registry("https://guild.example/i.json")
        assert "already in the list" in (host.add_registry("https://Guild.example/i.json") or "")
        assert "built-in" in (host.add_registry(DEFAULT_REGISTRY_URL) or "")

    def test_remove_a_user_registry(self, make_host, settings: Settings) -> None:
        host = make_host()
        host.add_registry("https://guild.example/i.json")
        assert host.remove_registry("https://guild.example/i.json") is True
        assert settings.plugins.registries == []
        assert host.remove_registry("https://guild.example/i.json") is False  # already gone

    def test_the_default_can_never_be_removed(self, make_host) -> None:
        from nparseplus.core.plugins.registry import DEFAULT_REGISTRY_URL

        host = make_host()
        assert host.remove_registry(DEFAULT_REGISTRY_URL) is False
        assert any(registry.is_default for registry in host.registries())

    def test_the_default_can_be_unticked_and_it_persists(
        self, make_host, settings: Settings, tmp_path
    ) -> None:
        from nparseplus.config.settings import load_settings, save_settings
        from nparseplus.core.plugins.registry import DEFAULT_REGISTRY_URL

        host = make_host()
        host.set_registry_enabled(DEFAULT_REGISTRY_URL, False)
        assert settings.plugins.default_registry_enabled is False
        assert host.enabled_registries() == []

        path = tmp_path / "settings.json"
        save_settings(settings, path)
        assert load_settings(path).plugins.default_registry_enabled is False

    def test_unticking_a_user_registry(self, make_host, settings: Settings) -> None:
        host = make_host()
        host.add_registry("https://guild.example/i.json")
        host.set_registry_enabled("https://guild.example/i.json", False)
        assert settings.plugins.registries[0].enabled is False
        assert [r.url for r in host.enabled_registries()] == [host.registries()[0].url]

    @staticmethod
    def _install_result():
        from nparseplus.core.plugins.install import InstallResult
        from nparseplus_sdk import PluginMeta

        return InstallResult(
            ok=True,
            meta=PluginMeta(id="demo", name="Demo", version="1.0.0"),
            source_url="https://x.example/demo.zip",
        )

    def test_record_install_stores_the_vouching_registry(
        self, make_host, settings: Settings
    ) -> None:
        make_host().record_install(
            self._install_result(), registry_url="https://guild.example/i.json"
        )
        assert settings.plugins.entries["demo"].registry_url == "https://guild.example/i.json"

    def test_a_plain_url_install_records_no_registry(self, make_host, settings: Settings) -> None:
        make_host().record_install(self._install_result())
        assert settings.plugins.entries["demo"].registry_url == ""

    def test_forget_drops_the_vouching_record_too(self, make_host, settings: Settings) -> None:
        host = make_host()
        host.record_install(self._install_result(), registry_url="https://guild.example/i.json")
        host.forget("demo")
        assert "demo" not in settings.plugins.entries
