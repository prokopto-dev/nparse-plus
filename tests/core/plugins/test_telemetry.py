"""Rolling per-plugin statistics: the numbers and the strings (#132)."""

from __future__ import annotations

from time import perf_counter

from nparseplus.core.plugins.telemetry import (
    MAX_SAMPLES,
    RATE_WINDOW_S,
    Channel,
    MetricsCollector,
    PluginMetrics,
    format_note,
    format_tooltip,
)


def _record(channel: Channel, durations: list[float], *, at: float | None = None) -> None:
    """Record with stamps near the real clock, because ``snapshot`` reads it.

    ``Channel.record`` takes the caller's ``perf_counter`` so the rate window
    costs no extra clock read; a test feeding it arbitrary stamps would see
    the window rolled clean away by the time it asks.
    """
    start = perf_counter() if at is None else at
    for offset, duration in enumerate(durations):
        channel.record(duration, start + offset * 1e-4)


def test_empty_channel_reports_nothing() -> None:
    snapshot = Channel("handler").snapshot()
    assert snapshot.calls == 0
    assert snapshot.recorded is False
    assert snapshot.mean_ms == 0.0


def test_mean_and_worst_are_over_every_call() -> None:
    channel = Channel("handler")
    _record(channel, [0.001, 0.002, 0.003])
    snapshot = channel.snapshot()
    assert snapshot.calls == 3
    assert round(snapshot.mean_ms, 6) == 2.0
    assert round(snapshot.worst_ms, 6) == 3.0


def test_percentiles_use_nearest_rank() -> None:
    channel = Channel("handler")
    _record(channel, [n / 1000.0 for n in range(1, 101)])  # 1ms .. 100ms
    snapshot = channel.snapshot()
    assert snapshot.samples == 100
    assert round(snapshot.p50_ms) == 50
    assert round(snapshot.p95_ms) == 95
    assert round(snapshot.p99_ms) == 99


def test_percentiles_roll_but_worst_does_not() -> None:
    """The ring is the *recent* window; ``worst`` is the whole run.

    A plugin that stalled once at launch and has been fine since should read
    as fine at p95 — and should still be able to tell you about the stall.
    """
    channel = Channel("handler")
    channel.record(5.0, 1000.0)  # one 5-second horror
    _record(channel, [0.0001] * (MAX_SAMPLES + 10))
    snapshot = channel.snapshot()
    assert snapshot.samples == MAX_SAMPLES
    assert snapshot.p99_ms < 1.0  # the stall has rolled out of the window
    assert round(snapshot.worst_ms) == 5000  # ...but is not forgotten


def test_rate_is_events_per_second_over_the_window() -> None:
    channel = Channel("handler")
    # 30 calls inside one bucket; the window is RATE_WINDOW_S wide.
    _record(channel, [0.0] * 30)
    snapshot = channel.snapshot()
    assert snapshot.rate_per_s > 0
    assert snapshot.rate_per_s <= 30.0 / RATE_WINDOW_S + 1e-9


def test_rate_decays_to_zero_when_nothing_arrives() -> None:
    channel = Channel("handler")
    now = perf_counter()
    _record(channel, [0.0] * 10, at=now)
    # Read the rate far in the future: every bucket is stale, so it is zero
    # even though the call count is not.
    assert channel._rate.rate(now + RATE_WINDOW_S * 3) == 0.0
    assert channel.snapshot().calls == 10


def test_errors_and_drops_are_counted_separately_from_calls() -> None:
    channel = Channel("handler")
    _record(channel, [0.001])
    channel.note_error()
    channel.note_dropped()
    snapshot = channel.snapshot()
    assert (snapshot.calls, snapshot.errors, snapshot.dropped) == (1, 1, 1)


def test_reset_keeps_the_same_object() -> None:
    """Wrappers capture the channel, so reset must never rebind it."""
    metrics = PluginMetrics("demo")
    handlers = metrics.handlers
    record = metrics.ticks.record
    _record(metrics.handlers, [0.001, 0.002])
    metrics.reset()
    assert metrics.handlers is handlers
    assert metrics.handlers.snapshot().calls == 0
    record(0.003, 1000.0)  # the bound method from before the reset still lands
    assert metrics.ticks.snapshot().calls == 1


def test_disabling_only_gates_handlers_and_parsers() -> None:
    """Ticks are timed by the driver's watchdog either way, so they stay on."""
    metrics = PluginMetrics("demo")
    metrics.set_enabled(False)
    assert metrics.handlers.enabled is False
    assert metrics.parsers.enabled is False
    assert metrics.ticks.enabled is True


def test_busy_fraction_is_a_share_of_one_thread() -> None:
    metrics = PluginMetrics("demo")
    metrics._started -= 10.0  # pretend ten seconds have passed
    _record(metrics.handlers, [0.5, 0.5])
    snapshot = metrics.snapshot()
    assert 0.09 < snapshot.busy_fraction < 0.11


def test_collector_hands_out_one_metrics_per_plugin_and_resets_on_reuse() -> None:
    collector = MetricsCollector(enabled=True)
    first = collector.for_plugin("demo")
    _record(first.handlers, [0.001])
    again = collector.for_plugin("demo")
    assert again is first
    assert again.handlers.snapshot().calls == 0  # a re-activation is a fresh run


def test_collector_toggle_reaches_existing_and_future_plugins() -> None:
    collector = MetricsCollector(enabled=True)
    existing = collector.for_plugin("one")
    collector.set_enabled(False)
    later = collector.for_plugin("two")
    assert existing.handlers.enabled is False
    assert later.handlers.enabled is False
    assert collector.enabled is False


def test_forget_drops_a_plugins_numbers() -> None:
    collector = MetricsCollector()
    collector.for_plugin("demo")
    collector.forget("demo")
    assert collector.get("demo") is None
    assert collector.snapshots() == {}


# --- the strings the manager renders ---------------------------------------
def test_note_says_nothing_when_collection_is_off() -> None:
    metrics = PluginMetrics("demo", enabled=False)
    assert format_note(metrics.snapshot(), collecting=False) == "not collecting"


def test_note_is_a_dash_before_anything_has_run() -> None:
    assert format_note(None) == "—"
    assert format_note(PluginMetrics("demo").snapshot()) == "—"


def test_note_leads_with_traffic_then_cost() -> None:
    metrics = PluginMetrics("demo")
    _record(metrics.handlers, [0.0002] * 20 + [0.004])
    note = format_note(metrics.snapshot())
    assert "ev/s" in note
    assert note.index("avg") < note.index("p95") < note.index("worst")
    assert "of the driver thread" in note


def test_note_omits_what_did_not_happen() -> None:
    """No errors, no drops, no queue: no clauses about them."""
    metrics = PluginMetrics("demo")
    _record(metrics.handlers, [0.001])
    note = format_note(metrics.snapshot())
    assert "error" not in note
    assert "dropped" not in note
    assert "queue" not in note


def test_note_names_errors_drops_and_a_queue_when_there_is_one() -> None:
    metrics = PluginMetrics("demo")
    _record(metrics.handlers, [0.001])
    metrics.handlers.note_error()
    metrics.ticks.note_dropped()
    note = format_note(metrics.snapshot(queue_depth=3))
    assert "1 error" in note
    assert "1 dropped" in note
    assert "queue 3" in note


def test_note_covers_every_channel_that_ran() -> None:
    metrics = PluginMetrics("demo")
    _record(metrics.handlers, [0.001])
    _record(metrics.parsers, [0.0001])
    _record(metrics.ticks, [0.002])
    note = format_note(metrics.snapshot())
    assert "parser avg" in note
    assert "tick avg" in note


def test_tooltip_spells_out_the_windows_and_the_missing_queue() -> None:
    metrics = PluginMetrics("demo")
    _record(metrics.handlers, [0.001] * 5)
    tip = format_tooltip(metrics.snapshot())
    assert f"last {MAX_SAMPLES} calls" in tip
    assert "p99" in tip
    assert "no per-plugin queue" in tip


def test_tooltip_is_empty_with_nothing_to_say() -> None:
    assert format_tooltip(None) == ""
    assert format_tooltip(PluginMetrics("demo").snapshot()) == ""


def test_durations_drop_to_microseconds_where_plugins_actually_live() -> None:
    """A 40 us handler must not read as "0.00 ms" beside a 400 us one."""
    from nparseplus.core.plugins.telemetry import _ms

    assert _ms(250.0) == "250 ms"
    assert _ms(12.34) == "12.3 ms"
    assert _ms(1.234) == "1.23 ms"
    assert _ms(0.04) == "40 µs"
    assert _ms(0.004) == "4.0 µs"
    assert _ms(0.0004) == "0.40 µs"
