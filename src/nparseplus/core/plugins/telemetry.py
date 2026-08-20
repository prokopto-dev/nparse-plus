"""Per-plugin rolling performance statistics (#132, Phase 0 of #131).

The driver thread runs the log tail, the parser chain, every timer countdown
and — inline — every plugin subscription, parser and tick. ``core/driver.py``
budgets ticks (``TICK_BUDGET_S``) and evicts a repeat offender; subscribers
and parsers are not timed at all, so today nobody can answer "which add-on is
costing me frames" with a number. This module is that number, and nothing
else: **measurement only**. It changes no scheduling decision, drops nothing,
and defers nothing. Phases 1-4 of #131 are what act on what this records.

Three properties shape the whole design.

**It must not cost what it measures.** Two facts do the work. First, only
*plugin* callbacks are ever wrapped — the app's own handlers, parsers and
ticks go through the same code they always did, so the no-plugin case (the
common one) keeps the property ``core/driver.py`` is careful about: no
per-callback clock reads on the app's hottest loop. ``tests/core/plugins/
test_telemetry_cost.py`` asserts that structurally by counting
``perf_counter`` calls. Second, when a plugin *is* loaded, ``enabled`` is one
attribute read on a ``__slots__`` object between the caller and the callback,
and that is the entire cost of collection being off.

**Ticks are timed for free.** The driver already reads the clock around every
supervised tick for the watchdog, so ``record`` is handed that elapsed value
rather than taking its own — which is why the tick channel is never gated:
turning it off would save nothing and lose a measurement already paid for.

**Writers are the driver thread; the reader is the GUI.** Deliberately
lock-free. ``record`` is on the hot path and a lock would roughly double what
collection costs, while the worst a torn read can do is make one refresh of
one row slightly wrong — a lost increment, or a percentile computed over a
ring that moved underneath it. Nothing here feeds a decision, so that is the
right trade; ``snapshot`` copies before it computes so a reader can never see
a half-written list. (A plugin publishing on its own thread would make
``record`` genuinely concurrent. Same answer: a count, not a crash.)

The one number that is not rolling is ``busy_fraction`` — total time in this
plugin's callbacks over wall-clock since the plugin activated. It is an
approximation of one plugin's share of ONE thread, not of the machine: 0.5
means the driver thread spent half its life inside this add-on, which is the
question worth asking here.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from time import monotonic, perf_counter

# Percentiles are computed over the most recent samples rather than all of
# them: a plugin that was slow for the first minute after launch and is fine
# now should read as fine now. 512 floats is ~4 KB per channel, and at the
# ~100 Hz an event-heavy plugin sees it is a window of a few seconds under
# combat and much longer when idle.
MAX_SAMPLES = 512

# Rate is bucketed rather than derived from a timestamp ring: one integer
# increment per call instead of another list write, and an idle channel
# decays to zero on read because the buckets roll forward whether or not
# anything was recorded.
RATE_BUCKET_S = 1.0
RATE_BUCKETS = 15
RATE_WINDOW_S = RATE_BUCKET_S * RATE_BUCKETS


class _RateWindow:
    """Bucketed events-per-second over the last ``RATE_WINDOW_S``."""

    __slots__ = ("_buckets", "_slot")

    def __init__(self) -> None:
        self._buckets = [0] * RATE_BUCKETS
        self._slot = 0

    def bump(self, now: float) -> None:
        slot = int(now // RATE_BUCKET_S)
        if slot != self._slot:
            self._roll(slot)
        self._buckets[slot % RATE_BUCKETS] += 1

    def rate(self, now: float) -> float:
        """Events/sec, rolling the window forward first so idle reads decay."""
        slot = int(now // RATE_BUCKET_S)
        if slot != self._slot:
            self._roll(slot)
        return sum(self._buckets) / RATE_WINDOW_S

    def _roll(self, slot: int) -> None:
        gap = slot - self._slot
        if gap < 0 or gap >= RATE_BUCKETS:
            # Either the clock went backwards (it should not: perf_counter is
            # monotonic) or nothing has been recorded for a whole window. Both
            # mean every surviving bucket is stale.
            self._buckets = [0] * RATE_BUCKETS
            self._slot = slot
            return
        for step in range(1, gap + 1):
            self._buckets[(self._slot + step) % RATE_BUCKETS] = 0
        self._slot = slot


@dataclass(frozen=True, slots=True)
class ChannelSnapshot:
    """One channel's stats at one instant. Plain data; safe to hand the GUI."""

    kind: str
    calls: int = 0
    errors: int = 0
    dropped: int = 0
    coalesced: int = 0
    rate_per_s: float = 0.0
    mean_ms: float = 0.0
    p50_ms: float = 0.0
    p95_ms: float = 0.0
    p99_ms: float = 0.0
    worst_ms: float = 0.0
    total_s: float = 0.0
    # How many samples the percentiles were computed over — a p99 over 7
    # samples is not a p99, and a reader deserves to be able to tell.
    samples: int = 0

    @property
    def recorded(self) -> bool:
        return self.calls > 0


class Channel:
    """Rolling stats for one kind of plugin callback (handler/parser/tick).

    ``enabled`` is read by the wrapper *before* it reads any clock, so it is
    the gate: False means collection costs one attribute read and nothing
    else. It is set by the owning :class:`PluginMetrics`, never here, so one
    settings toggle moves every channel at once.
    """

    __slots__ = (
        "_next",
        "_rate",
        "_ring",
        "_wrapped",
        "calls",
        "coalesced",
        "dropped",
        "enabled",
        "errors",
        "kind",
        "total_s",
        "worst_s",
    )

    def __init__(self, kind: str, *, enabled: bool = True) -> None:
        self.kind = kind
        self.enabled = enabled
        self.calls = 0
        self.errors = 0
        self.dropped = 0
        # Reserved for #131 Phase 3 (batched/coalesced delivery). Nothing
        # coalesces per plugin today, so it stays 0 and the note omits it
        # rather than printing a zero that reads as "coalescing is on".
        self.coalesced = 0
        self.total_s = 0.0
        self.worst_s = 0.0
        self._ring = [0.0] * MAX_SAMPLES
        self._next = 0
        self._wrapped = False
        self._rate = _RateWindow()

    def record(self, elapsed_s: float, started: float) -> None:
        """Note one call. ``started`` is the ``perf_counter`` the caller took.

        Reusing the caller's clock read is the point: the wrapper needs a
        start stamp anyway, so the rate window costs no additional syscall.
        """
        self.calls += 1
        self.total_s += elapsed_s
        if elapsed_s > self.worst_s:
            self.worst_s = elapsed_s
        index = self._next
        self._ring[index] = elapsed_s
        index += 1
        if index == MAX_SAMPLES:
            index = 0
            self._wrapped = True
        self._next = index
        self._rate.bump(started)

    def reset(self) -> None:
        """Zero everything IN PLACE — never rebind, never replace.

        A wrapper installed at ``activate`` captures this exact object (a
        bound ``record``, a parser holding the channel), so handing out a new
        one would leave the plugin writing into a channel nobody reads.
        """
        self.calls = 0
        self.errors = 0
        self.dropped = 0
        self.coalesced = 0
        self.total_s = 0.0
        self.worst_s = 0.0
        self._next = 0
        self._wrapped = False
        self._rate = _RateWindow()

    def note_error(self) -> None:
        self.errors += 1

    def note_dropped(self, count: int = 1) -> None:
        self.dropped += count

    def snapshot(self) -> ChannelSnapshot:
        # Copy first: record() may be running on the driver thread right now.
        samples = self._ring[:MAX_SAMPLES] if self._wrapped else self._ring[: self._next]
        calls = self.calls
        rate = self._rate.rate(perf_counter())
        if not samples:
            return ChannelSnapshot(
                kind=self.kind,
                calls=calls,
                errors=self.errors,
                dropped=self.dropped,
                coalesced=self.coalesced,
                rate_per_s=rate,
            )
        ordered = sorted(samples)
        return ChannelSnapshot(
            kind=self.kind,
            calls=calls,
            errors=self.errors,
            dropped=self.dropped,
            coalesced=self.coalesced,
            rate_per_s=rate,
            mean_ms=(self.total_s / calls) * 1000.0 if calls else 0.0,
            p50_ms=_percentile(ordered, 0.50) * 1000.0,
            p95_ms=_percentile(ordered, 0.95) * 1000.0,
            p99_ms=_percentile(ordered, 0.99) * 1000.0,
            worst_ms=self.worst_s * 1000.0,
            total_s=self.total_s,
            samples=len(ordered),
        )


def _percentile(ordered: list[float], fraction: float) -> float:
    """Nearest-rank percentile over an already-sorted list (never empty).

    Nearest-rank (``ceil(p * N)``) rather than interpolated on purpose: every
    value it can answer is a duration something actually took, which is what
    makes "p99" and "worst" comparable when the sample count is small — an
    interpolated p99 over 40 samples is a number nothing measured.
    """
    rank = max(1, math.ceil(fraction * len(ordered)))
    return ordered[min(rank, len(ordered)) - 1]


@dataclass(frozen=True, slots=True)
class PluginStatsSnapshot:
    """Everything the manager row needs about one plugin, as plain data."""

    plugin_id: str
    handlers: ChannelSnapshot
    parsers: ChannelSnapshot
    ticks: ChannelSnapshot
    busy_fraction: float = 0.0
    observed_s: float = 0.0
    # Depth of this plugin's inbound queue. None means "there is no queue" —
    # which is the truth until #131 Phase 2 builds one, and is why this is
    # not simply 0: a zero would read as a queue that happens to be empty.
    queue_depth: int | None = None

    @property
    def recorded(self) -> bool:
        return self.handlers.recorded or self.parsers.recorded or self.ticks.recorded

    @property
    def dropped(self) -> int:
        return self.handlers.dropped + self.parsers.dropped + self.ticks.dropped

    @property
    def errors(self) -> int:
        return self.handlers.errors + self.parsers.errors + self.ticks.errors


class PluginMetrics:
    """The three channels belonging to one plugin, plus its CPU share."""

    __slots__ = ("_started", "enabled", "handlers", "parsers", "plugin_id", "ticks")

    def __init__(self, plugin_id: str, *, enabled: bool = True) -> None:
        self.plugin_id = plugin_id
        self.enabled = enabled
        self.handlers = Channel("handler", enabled=enabled)
        self.parsers = Channel("parser", enabled=enabled)
        # Ticks are timed by the driver's watchdog whether or not collection
        # is on, so this channel is never gated: skipping it would save
        # nothing and lose the one measurement that is already paid for.
        self.ticks = Channel("tick", enabled=True)
        self._started = monotonic()

    def set_enabled(self, enabled: bool) -> None:
        self.enabled = enabled
        self.handlers.enabled = enabled
        self.parsers.enabled = enabled

    def reset(self) -> None:
        """Start over — used when a plugin is re-activated in one session.

        A re-enabled add-on is a fresh run; carrying the old ring over would
        attribute the previous run's worst case to it (the same reasoning
        that clears ``ctx.tick_dropped`` on unwind). In place, for the reason
        ``Channel.reset`` gives.
        """
        self.handlers.reset()
        self.parsers.reset()
        self.ticks.reset()
        self._started = monotonic()

    def snapshot(self, *, queue_depth: int | None = None) -> PluginStatsSnapshot:
        handlers = self.handlers.snapshot()
        parsers = self.parsers.snapshot()
        ticks = self.ticks.snapshot()
        observed = max(monotonic() - self._started, 1e-9)
        busy = (handlers.total_s + parsers.total_s + ticks.total_s) / observed
        return PluginStatsSnapshot(
            plugin_id=self.plugin_id,
            handlers=handlers,
            parsers=parsers,
            ticks=ticks,
            busy_fraction=busy,
            observed_s=observed,
            queue_depth=queue_depth,
        )


class MetricsCollector:
    """One per app run; hands out (and remembers) a ``PluginMetrics`` per id.

    Owned by ``PluginHost``, which is only built when add-ons are enabled —
    so with plugins off this class is never even imported.
    """

    def __init__(self, *, enabled: bool = True) -> None:
        self._enabled = enabled
        self._plugins: dict[str, PluginMetrics] = {}

    @property
    def enabled(self) -> bool:
        return self._enabled

    def set_enabled(self, enabled: bool) -> None:
        """Flip collection for every plugin, present and future, at once."""
        self._enabled = enabled
        for metrics in self._plugins.values():
            metrics.set_enabled(enabled)

    def for_plugin(self, plugin_id: str) -> PluginMetrics:
        """The plugin's metrics, reset if it already had some.

        Called at activation, which is also the moment a re-enabled plugin
        gets a new context — so "already had some" means "this is its second
        run this session" and the old numbers describe the first one.
        """
        existing = self._plugins.get(plugin_id)
        if existing is not None:
            existing.set_enabled(self._enabled)
            existing.reset()
            return existing
        metrics = PluginMetrics(plugin_id, enabled=self._enabled)
        self._plugins[plugin_id] = metrics
        return metrics

    def get(self, plugin_id: str) -> PluginMetrics | None:
        return self._plugins.get(plugin_id)

    def forget(self, plugin_id: str) -> None:
        self._plugins.pop(plugin_id, None)

    def snapshots(self) -> dict[str, PluginStatsSnapshot]:
        return {plugin_id: m.snapshot() for plugin_id, m in self._plugins.items()}


def _ms(value: float) -> str:
    """A duration given in MILLISECONDS, rendered at a readable scale.

    It drops to microseconds below 1 ms because that is where a
    well-behaved plugin lives: a handler that costs 40 us is the normal
    case, and printing it as "0.00 ms" throws away the only digits that
    distinguish it from one costing ten times as much.
    """
    if value >= 100.0:
        return f"{value:.0f} ms"
    if value >= 10.0:
        return f"{value:.1f} ms"
    if value >= 1.0:
        return f"{value:.2f} ms"
    micros = value * 1000.0
    if micros >= 10.0:
        return f"{micros:.0f} µs"
    if micros >= 1.0:
        return f"{micros:.1f} µs"
    return f"{micros:.2f} µs"


def format_note(snapshot: PluginStatsSnapshot | None, *, collecting: bool = True) -> str:
    """The manager row's performance cell. Pure, so it is tested without Qt.

    Reads left to right as the note in #131's worked example does: how much
    traffic, what it costs per callback, what the worst case was, and then
    only the facts that are non-zero — a row that says nothing about errors
    is a row that had none.
    """
    if not collecting:
        return "not collecting"
    if snapshot is None or not snapshot.recorded:
        return "—"
    parts: list[str] = []
    handlers = snapshot.handlers
    if handlers.recorded:
        parts.append(f"{handlers.rate_per_s:.1f} ev/s")
        parts.append(f"avg {_ms(handlers.mean_ms)}")
        parts.append(f"p95 {_ms(handlers.p95_ms)}")
        parts.append(f"worst {_ms(handlers.worst_ms)}")
    if snapshot.parsers.recorded:
        parts.append(f"parser avg {_ms(snapshot.parsers.mean_ms)}")
    if snapshot.ticks.recorded:
        parts.append(f"tick avg {_ms(snapshot.ticks.mean_ms)}")
    if snapshot.queue_depth is not None:
        parts.append(f"queue {snapshot.queue_depth}")
    if snapshot.dropped:
        parts.append(f"{snapshot.dropped} dropped")
    if snapshot.errors:
        parts.append(f"{snapshot.errors} error{'s' if snapshot.errors != 1 else ''}")
    parts.append(f"{snapshot.busy_fraction * 100:.1f}% of the driver thread")
    return " · ".join(parts)


def format_tooltip(snapshot: PluginStatsSnapshot | None) -> str:
    """The long form behind the cell: every channel, percentiles and counts."""
    if snapshot is None or not snapshot.recorded:
        return ""
    lines = [
        f"Measured over {snapshot.observed_s:.0f}s "
        f"(percentiles over the last {MAX_SAMPLES} calls, "
        f"rate over the last {RATE_WINDOW_S:.0f}s)."
    ]
    for channel in (snapshot.handlers, snapshot.parsers, snapshot.ticks):
        if not channel.recorded:
            continue
        lines.append(
            f"{channel.kind}: {channel.calls} calls, {channel.rate_per_s:.1f}/s, "
            f"mean {_ms(channel.mean_ms)}, p50 {_ms(channel.p50_ms)}, "
            f"p95 {_ms(channel.p95_ms)}, p99 {_ms(channel.p99_ms)}, "
            f"worst {_ms(channel.worst_ms)}"
        )
    if snapshot.queue_depth is None:
        lines.append("Queue depth: no per-plugin queue in this release (#131 Phase 2).")
    return "\n".join(lines)
