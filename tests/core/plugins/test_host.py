"""PluginHost lifecycle: classification, consent, activation, isolation."""

from __future__ import annotations

from pathlib import Path

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


def test_set_enabled_toggles_entry(make_host, plugins_dir: Path, settings: Settings) -> None:
    write_plugin(plugins_dir, "flip.py", plugin_id="flip")
    approve(settings, "flip")
    host = make_host()
    host.discover_and_load()
    host.set_enabled("flip", False)
    assert settings.plugins.entries["flip"].enabled is False
    host.set_enabled("flip", True)
    assert settings.plugins.entries["flip"].enabled is True
