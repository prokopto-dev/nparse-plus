"""ToD Window — arm a variable respawn ("pop") window from a time of death.

Big mobs do not respawn on a fixed clock. After time-of-death a base time
elapses, and only then does the mob become poppable — at any moment until a
latest-possible time. Trakanon is TOD + 4.5 days, then a 12-hour window. The
app models this but ships no per-mob data and no way to type a TOD in; a
plugin supplies the numbers, and this is the reference for how.

Say ``tod trakanon`` in game and it arms Trakanon's window from right now.
The row counts down to the window opening, then flips **in place** to an
orange ``POP hh:mm:ss`` counting to the latest possible pop, and disappears
when the window closes.

The two events are the point of the seam: ``TimerWindowOpenedEvent`` fires
on the driver tick that crosses ``ends_at`` (once — a restore after camping
preserves the stamp rather than re-firing it), and ``TimerWindowClosedEvent``
when the window runs out. Subscribe to those rather than polling the row.

Note the group: only ``  Mob Timers`` survives camping, so a window armed in
a group of your own is session-only — the same rule trigger timers follow.

Install: add-ons are off by default — tick Settings > Advanced > Enable
plugins (add-ons) and restart nParse+ first. Then copy this file into your
plugins folder (tray > Open Plugins Folder), or use Settings > Plugins >
Install from file, and restart again to be asked for consent.
Check it from a dev environment with: ``nparseplus-plugin validate tod_window.py``
"""

from __future__ import annotations

from typing import Any

from nparseplus_sdk import NParsePlugin, PluginContext, PluginMeta

TRIGGER_PREFIX = "tod "
MOB_TIMER_GROUP = "  Mob Timers"  # the one group that survives camping

#: mob -> (base seconds before the window opens, window length in seconds).
#: Real P99 figures; shrink them if you want to watch the crossover happen.
RESPAWNS: dict[str, tuple[float, float]] = {
    "trakanon": (4.5 * 24 * 3600, 12 * 3600),
    "faydedar": (3.5 * 24 * 3600, 8 * 3600),
    "severilous": (4.5 * 24 * 3600, 12 * 3600),
}


class TodWindowPlugin(NParsePlugin):
    meta = PluginMeta(
        id="tod-window",
        name="ToD Window",
        version="1.0.0",
        description="Say 'tod <mob>' to arm a variable respawn (pop) window.",
        author="nParse+ examples",
        # add_window_timer and the two window events are SDK 1.3.
        requires_sdk=">=1.3,<2",
    )

    def activate(self, ctx: PluginContext) -> None:
        # Host classes import lazily so the validate CLI can activate this
        # plugin even in a bare dev environment (see nparseplus_sdk.events).
        try:
            from nparseplus_sdk.events import (
                CommsChannel,
                CommsEvent,
                TimerWindowClosedEvent,
                TimerWindowOpenedEvent,
            )
        except ImportError:
            ctx.logger.warning("host events unavailable (standalone run); inert")
            return

        def on_comms(event: Any) -> None:
            if event.channel != CommsChannel.SAY or event.sender != "You":
                return
            said = event.content.strip().lower()
            if not said.startswith(TRIGGER_PREFIX):
                return
            mob = said[len(TRIGGER_PREFIX) :].strip()
            respawn = RESPAWNS.get(mob)
            if respawn is None:
                ctx.speaker.speak(f"I do not know {mob}")
                return
            base_seconds, window_seconds = respawn
            ctx.add_window_timer(
                f"--Dead-- {mob.title()}",
                group=MOB_TIMER_GROUP,
                # The log line's own clock, not the wall clock: the whole
                # pipeline is anchored to the log.
                started_at=event.timestamp,
                base_seconds=base_seconds,
                window_seconds=window_seconds,
            )
            ctx.speaker.speak(f"{mob} time of death recorded")

        def on_open(event: Any) -> None:
            ctx.logger.info("%s pop window open until %s", event.name, event.closes_at)
            ctx.speaker.speak(f"{event.name} window is open")

        def on_close(event: Any) -> None:
            ctx.logger.info("%s pop window closed at %s", event.name, event.closed_at)

        ctx.subscribe(CommsEvent, on_comms)
        ctx.subscribe(TimerWindowOpenedEvent, on_open)
        ctx.subscribe(TimerWindowClosedEvent, on_close)
        ctx.logger.info("tod-window ready — say 'tod trakanon' in game")


def create_plugin() -> TodWindowPlugin:
    return TodWindowPlugin()
