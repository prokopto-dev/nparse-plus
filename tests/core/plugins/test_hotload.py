"""Enabling and disabling a plugin without a restart (#45).

The shape of every round trip here is ``test_shutdown_unwinds_registrations``'s:
count the bus subscribers, driver ticks and pipeline parsers, assert +1 while
the plugin runs and exactly the baseline once it does not. What is new is that
it happens mid-session, so a leak shows up as an add-on the user switched off
still parsing lines.

Nothing sleeps on the 100 ms poll. The backend's driver is never started, so
``submit_to_driver`` applies inline; the one test that needs the *running*
behaviour opens the command gate by hand and calls ``_iterate`` itself, the
way ``tests/core/test_driver_inbox.py`` does.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

from nparseplus.config.settings import Settings
from nparseplus.core.driver import LogDriver
from nparseplus.core.events import LineEvent
from nparseplus.core.timers import TRIGGER_TIMER_GROUP, TimerRow

from .conftest import approve, write_plugin

# Registers one of each of the three things unwind() reverses.
WIRED = (
    "        from nparseplus.core.events import LineEvent\n"
    "        ctx.subscribe(LineEvent, lambda e: None)\n"
    "        ctx.add_tick(lambda now: None)\n"
    "        class P:\n"
    "            def handle(self, line, pctx):\n"
    "                return False\n"
    "        ctx.add_parser(P())"
)

# Two timer rows through the two doors a plugin has: the raw service and the
# pop-window helper.
ARMS_TIMERS = (
    "        from datetime import datetime, timedelta\n"
    "        from nparseplus.core.timers import TimerRow\n"
    "        when = datetime(2026, 7, 15, 12, 0, 0)\n"
    "        ctx.timers.add_timer(\n"
    "            TimerRow(\n"
    "                name='Plugin Countdown',\n"
    "                group='  Custom Timers',\n"
    "                updated_at=when,\n"
    "                ends_at=when + timedelta(hours=1),\n"
    "                total_duration_s=3600.0,\n"
    "            )\n"
    "        )\n"
    "        ctx.add_window_timer(\n"
    "            'Plugin Pop',\n"
    "            group='  Mob Timers',\n"
    "            started_at=when,\n"
    "            base_seconds=600.0,\n"
    "            window_seconds=300.0,\n"
    "        )"
)


def registrations(backend) -> tuple[int, int, int]:
    """(bus subscribers, driver ticks, pipeline parsers) — the leak detector."""
    return (
        len(backend.bus._subscribers[LineEvent]),
        len(backend.driver.on_tick),
        len(backend.pipeline._parsers),
    )


@contextmanager
def accepting_commands(driver: LogDriver) -> Iterator[None]:
    """The state ``start()`` leaves the driver in, without the poll loop."""
    with driver._command_lock:
        driver._accepting_commands = True
    try:
        yield
    finally:
        with driver._command_lock:
            driver._accepting_commands = False


def loaded_for(host, plugin_id: str):
    return next(p for p in host.statuses() if p.plugin_id == plugin_id)


# --- the round trip ---------------------------------------------------------


def test_enable_activates_and_disable_unwinds(
    make_host, plugins_dir: Path, settings: Settings, backend
) -> None:
    write_plugin(plugins_dir, "wired.py", plugin_id="wired", activate_body=WIRED)
    approve(settings, "wired", enabled=False)
    host = make_host()
    host.discover_and_load()
    host.activate_enabled()
    baseline = registrations(backend)
    assert loaded_for(host, "wired").status == "disabled"

    loaded = host.set_enabled("wired", True)
    assert loaded is not None and loaded.status == "active"
    assert registrations(backend) == tuple(n + 1 for n in baseline)
    assert settings.plugins.entries["wired"].enabled is True

    loaded = host.set_enabled("wired", False)
    assert loaded is not None and loaded.status == "disabled"
    assert loaded.context is None
    assert registrations(backend) == baseline
    assert settings.plugins.entries["wired"].enabled is False


def test_enabling_twice_activates_once(
    make_host, plugins_dir: Path, settings: Settings, backend
) -> None:
    """A second tick of an already-ticked box must not re-run activate()."""
    write_plugin(plugins_dir, "wired.py", plugin_id="wired", activate_body=WIRED)
    approve(settings, "wired", enabled=False)
    host = make_host()
    host.discover_and_load()
    baseline = registrations(backend)

    host.set_enabled("wired", True)
    host.set_enabled("wired", True)
    assert registrations(backend) == tuple(n + 1 for n in baseline)


def test_disable_then_enable_runs_the_plugin_again(
    make_host, plugins_dir: Path, settings: Settings, tmp_path: Path
) -> None:
    """A re-enabled plugin gets a real ``activate()`` and a fresh context."""
    marker = tmp_path / "activations.txt"
    write_plugin(
        plugins_dir,
        "counting.py",
        plugin_id="counting",
        activate_body=f"        open({str(marker)!r}, 'a').write('x')",
    )
    approve(settings, "counting")
    host = make_host()
    host.discover_and_load()
    host.activate_enabled()
    first_context = loaded_for(host, "counting").context
    assert marker.read_text() == "x"

    host.set_enabled("counting", False)
    host.set_enabled("counting", True)
    loaded = loaded_for(host, "counting")
    assert marker.read_text() == "xx"
    assert loaded.status == "active"
    assert loaded.context is not None and loaded.context is not first_context


def test_registration_changes_go_through_the_driver_inbox(
    make_host, plugins_dir: Path, settings: Settings, backend, tmp_path: Path
) -> None:
    """With the loop running, the tick and the parser wait for its next pass.

    The bus does not: ``EventBus.publish`` iterates a copy precisely so a
    handler may (un)subscribe during dispatch, so a subscription is safe to
    make on the spot. The other two are the driver's own state.
    """
    backend.driver.log_dir = tmp_path  # nothing to tail; keep _iterate cheap
    write_plugin(plugins_dir, "wired.py", plugin_id="wired", activate_body=WIRED)
    approve(settings, "wired", enabled=False)
    host = make_host()
    host.discover_and_load()
    subscribers, ticks, parsers = registrations(backend)

    with accepting_commands(backend.driver):
        host.set_enabled("wired", True)
        assert registrations(backend) == (subscribers + 1, ticks, parsers)
        backend.driver._iterate()
        assert registrations(backend) == (subscribers + 1, ticks + 1, parsers + 1)

        host.set_enabled("wired", False)
        assert registrations(backend) == (subscribers, ticks + 1, parsers + 1)
        backend.driver._iterate()
        assert registrations(backend) == (subscribers, ticks, parsers)


# --- what unwind() cannot reverse on its own --------------------------------


def test_disable_takes_the_plugins_timer_rows_with_it(
    make_host, plugins_dir: Path, settings: Settings, backend
) -> None:
    write_plugin(plugins_dir, "ticker.py", plugin_id="ticker", activate_body=ARMS_TIMERS)
    approve(settings, "ticker")
    when = datetime(2026, 7, 15, 12, 0, 0)
    backend.timers.add_timer(
        TimerRow(
            name="Ours",
            group=TRIGGER_TIMER_GROUP,
            updated_at=when,
            ends_at=when + timedelta(minutes=5),
            total_duration_s=300.0,
        )
    )
    host = make_host()
    host.discover_and_load()
    host.activate_enabled()
    assert {row.name for row in backend.timers.snapshot()} == {
        "Ours",
        "Plugin Countdown",
        "Plugin Pop",
    }
    assert {row.owner for row in backend.timers.snapshot() if row.name != "Ours"} == {"ticker"}

    host.set_enabled("ticker", False)
    assert [row.name for row in backend.timers.snapshot()] == ["Ours"]
    assert backend.timers.snapshot()[0].owner == ""


def test_a_second_plugins_rows_survive_the_first_being_disabled(
    make_host, plugins_dir: Path, settings: Settings, backend
) -> None:
    write_plugin(plugins_dir, "one.py", plugin_id="one", activate_body=ARMS_TIMERS)
    write_plugin(
        plugins_dir,
        "two.py",
        plugin_id="two",
        activate_body=(
            "        from datetime import datetime, timedelta\n"
            "        from nparseplus.core.timers import TimerRow\n"
            "        when = datetime(2026, 7, 15, 12, 0, 0)\n"
            "        ctx.timers.add_timer(\n"
            "            TimerRow(\n"
            "                name='Theirs',\n"
            "                group='  Custom Timers',\n"
            "                updated_at=when,\n"
            "                ends_at=when + timedelta(hours=2),\n"
            "                total_duration_s=7200.0,\n"
            "            )\n"
            "        )"
        ),
    )
    approve(settings, "one")
    approve(settings, "two")
    host = make_host()
    host.discover_and_load()
    host.activate_enabled()

    host.set_enabled("one", False)
    assert [row.name for row in backend.timers.snapshot()] == ["Theirs"]


def test_a_re_enabled_plugin_does_not_inherit_the_slow_tick_note(
    make_host, plugins_dir: Path, settings: Settings
) -> None:
    """The manager's "tick disabled (too slow)" belongs to one activation."""
    write_plugin(plugins_dir, "slow.py", plugin_id="slow", activate_body=WIRED)
    approve(settings, "slow")
    host = make_host()
    host.discover_and_load()
    host.activate_enabled()
    loaded = loaded_for(host, "slow")
    assert loaded.context is not None
    loaded.context.tick_dropped = "tick disabled (too slow)"

    host.set_enabled("slow", False)
    assert loaded.tick_dropped is None
    host.set_enabled("slow", True)
    assert loaded.status == "active"
    assert loaded.tick_dropped is None


# --- the UI teardown hook ---------------------------------------------------


def test_disable_asks_the_ui_to_drop_what_it_built(
    make_host, plugins_dir: Path, settings: Settings
) -> None:
    write_plugin(plugins_dir, "windowed.py", plugin_id="windowed", activate_body=WIRED)
    approve(settings, "windowed")
    host = make_host()
    torn: list[str] = []
    host.on_ui_teardown.append(lambda pid: (_ for _ in ()).throw(RuntimeError("qt boom")))
    host.on_ui_teardown.append(torn.append)
    host.discover_and_load()
    host.activate_enabled()

    host.set_enabled("windowed", False)  # a raising listener must not propagate
    assert torn == ["windowed"]


# --- failure isolation ------------------------------------------------------


def test_a_raise_during_hot_activate_lands_in_error_unwound(
    make_host, plugins_dir: Path, settings: Settings, backend
) -> None:
    write_plugin(
        plugins_dir,
        "boom.py",
        plugin_id="boom",
        activate_body=(
            "        from nparseplus.core.events import LineEvent\n"
            "        ctx.subscribe(LineEvent, lambda e: None)\n"
            "        ctx.add_tick(lambda now: None)\n"
            "        raise RuntimeError('activate boom')"
        ),
    )
    approve(settings, "boom", enabled=False)
    host = make_host()
    host.discover_and_load()
    baseline = registrations(backend)

    loaded = host.set_enabled("boom", True)  # must not raise
    assert loaded is not None and loaded.status == "error"
    assert loaded.error is not None and "activate boom" in loaded.error
    assert registrations(backend) == baseline


def test_a_raise_during_hot_deactivate_still_unwinds_and_tears_down(
    make_host, plugins_dir: Path, settings: Settings, backend
) -> None:
    write_plugin(
        plugins_dir,
        "rude.py",
        plugin_id="rude",
        activate_body=WIRED,
        deactivate_body="        raise RuntimeError('rude bye')",
    )
    approve(settings, "rude")
    host = make_host()
    torn: list[str] = []
    host.on_ui_teardown.append(torn.append)
    host.discover_and_load()
    baseline_with_plugin = registrations(backend)
    host.activate_enabled()
    assert registrations(backend) == tuple(n + 1 for n in baseline_with_plugin)

    loaded = host.set_enabled("rude", False)  # must not raise
    assert loaded is not None and loaded.status == "disabled"
    assert registrations(backend) == baseline_with_plugin
    assert torn == ["rude"]


# --- consent is not a checkbox ----------------------------------------------


def test_enabling_an_unconsented_plugin_does_not_activate_it(
    make_host, plugins_dir: Path, settings: Settings, backend
) -> None:
    write_plugin(plugins_dir, "newbie.py", plugin_id="newbie", activate_body=WIRED)
    host = make_host()
    host.discover_and_load()
    baseline = registrations(backend)

    loaded = host.set_enabled("newbie", True)
    assert loaded is not None and loaded.status == "pending_consent"
    assert registrations(backend) == baseline
    entry = settings.plugins.entries["newbie"]
    assert entry.enabled is True and entry.approved is False


def test_consent_can_still_be_given_after_a_decline(
    make_host, plugins_dir: Path, settings: Settings
) -> None:
    write_plugin(plugins_dir, "second.py", plugin_id="second")
    host = make_host()
    host.discover_and_load()
    host.record_consent("second", False)
    assert loaded_for(host, "second").status == "disabled"

    loaded = host.record_consent("second", True)
    assert loaded is not None and loaded.status == "ready"
    assert settings.plugins.entries["second"].enabled is True


def test_consent_keeps_the_provenance_the_install_recorded(
    make_host, plugins_dir: Path, settings: Settings
) -> None:
    """Answering the dialog must not erase who vouched for the artifact."""
    from nparseplus.core.plugins.install import InstallResult
    from nparseplus_sdk import PluginMeta

    write_plugin(plugins_dir, "vouched.py", plugin_id="vouched")
    host = make_host()
    host.record_install(
        InstallResult(
            ok=True,
            meta=PluginMeta(id="vouched", name="Vouched", version="1.0.0"),
            installed_path=plugins_dir / "vouched.py",
            source_url="https://example.test/vouched.zip",
            sha256="a" * 64,
        ),
        registry_url="https://registry.test/index.json",
    )
    host.discover_and_load()
    host.record_consent("vouched", True)

    entry = settings.plugins.entries["vouched"]
    assert entry.approved and entry.enabled
    assert entry.registry_url == "https://registry.test/index.json"
    assert entry.source_url == "https://example.test/vouched.zip"
    assert entry.sha256 == "a" * 64


# --- adopting a plugin installed this session -------------------------------


def test_a_plugin_installed_this_session_is_adopted_and_activatable(
    make_host, plugins_dir: Path, settings: Settings, backend
) -> None:
    host = make_host()
    host.discover_and_load()
    assert host.statuses() == []
    baseline = registrations(backend)

    path = write_plugin(plugins_dir, "fresh.py", plugin_id="fresh", activate_body=WIRED)
    loaded = host.adopt_installed(path)
    assert loaded is not None and loaded.status == "pending_consent"
    assert host.statuses() == [loaded]

    host.record_consent("fresh", True)
    assert host.activate_one("fresh") is loaded
    assert loaded.status == "active"
    assert registrations(backend) == tuple(n + 1 for n in baseline)


def test_adopting_a_path_twice_re_imports_nothing(
    make_host, plugins_dir: Path, settings: Settings
) -> None:
    """Import-once-per-session: the second adopt is the first one's row."""
    host = make_host()
    host.discover_and_load()
    path = write_plugin(plugins_dir, "again.py", plugin_id="again")
    first = host.adopt_installed(path)
    second = host.adopt_installed(path)
    assert first is not None and second is first
    assert len(host.statuses()) == 1


def test_an_adopted_plugin_claiming_a_live_id_is_a_duplicate(
    make_host, plugins_dir: Path, settings: Settings
) -> None:
    write_plugin(plugins_dir, "original.py", plugin_id="shared")
    approve(settings, "shared")
    host = make_host()
    host.discover_and_load()
    host.activate_enabled()

    path = write_plugin(plugins_dir, "impostor.py", plugin_id="shared", version="9.9.9")
    loaded = host.adopt_installed(path)
    assert loaded is not None and loaded.status == "duplicate"
    # The id still resolves to the plugin that claimed it first.
    assert host.set_enabled("shared", False) is not loaded


def test_adopting_something_that_is_not_a_plugin_answers_none(make_host, plugins_dir: Path) -> None:
    plugins_dir.mkdir(parents=True, exist_ok=True)
    stray = plugins_dir / "notes.txt"
    stray.write_text("not a plugin", encoding="utf-8")
    host = make_host()
    host.discover_and_load()

    assert host.adopt_installed(stray) is None
    assert host.adopt_installed(plugins_dir / "trash") is None
    assert host.statuses() == []


def test_an_adopted_plugin_that_fails_to_import_is_isolated(make_host, plugins_dir: Path) -> None:
    plugins_dir.mkdir(parents=True, exist_ok=True)
    broken = plugins_dir / "broken.py"
    broken.write_text("import nothing_here_at_all\n", encoding="utf-8")
    host = make_host()
    host.discover_and_load()

    loaded = host.adopt_installed(broken)  # must not raise
    assert loaded is not None and loaded.status == "error"


# --- unknown ids ------------------------------------------------------------


def test_lifecycle_calls_for_an_unknown_id_are_no_ops(make_host, settings: Settings) -> None:
    host = make_host()
    host.discover_and_load()
    assert host.activate_one("ghost") is None
    assert host.deactivate_one("ghost") is None
    assert host.record_consent("ghost", True) is None
    # set_enabled still records the wish — the plugin may be installed later.
    assert host.set_enabled("ghost", False) is None
    assert settings.plugins.entries["ghost"].enabled is False
