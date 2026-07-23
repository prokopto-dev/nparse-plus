"""Hello Timer — the smallest useful nParse+ plugin.

Say ``hello nparse`` in game and it starts a 30-second timer row in the
spell-timer window and speaks a greeting. This file is the reference for the
plugin anatomy: a ``PluginMeta``, an ``NParsePlugin`` subclass, and a
module-level ``create_plugin()`` factory.

Why ``subscribe`` and not ``add_parser``: the parser chain is
first-match-wins and the built-in comms parser consumes every chat line
(say/tell/auction/…), publishing a typed ``CommsEvent`` instead. Plugin
parsers only see lines no built-in claimed — right for novel log lines,
wrong for chat. Subscribe to the typed events (or the ``LineEvent``
firehose, which always fires) for anything the app already understands.

Install: copy this file into your nParse+ plugins folder (tray >
Open Plugins Folder), or Settings > Plugins > Install from file.
Check it from a dev environment with: ``nparseplus-plugin validate hello_timer.py``
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from nparseplus_sdk import NParsePlugin, PluginContext, PluginMeta

TRIGGER_TEXT = "hello nparse"
TIMER_SECONDS = 30
TIMER_GROUP = " Hello Timer"  # leading space: sorts with the built-in sections


class HelloTimerPlugin(NParsePlugin):
    meta = PluginMeta(
        id="hello-timer",
        name="Hello Timer",
        version="1.0.0",
        description="Say 'hello nparse' in game to start a 30s demo timer.",
        author="nParse+ examples",
        requires_sdk=">=1.0,<2",
    )

    def activate(self, ctx: PluginContext) -> None:
        # Host classes import lazily so the validate CLI can activate this
        # plugin even in a bare dev environment (see nparseplus_sdk.events).
        try:
            from nparseplus_sdk.events import CommsChannel, CommsEvent
        except ImportError:
            ctx.logger.warning("host events unavailable (standalone run); inert")
            return

        def on_comms(event: Any) -> None:
            if event.channel != CommsChannel.SAY or event.sender != "You":
                return
            if event.content.strip().lower() != TRIGGER_TEXT:
                return
            from nparseplus_sdk.timers import TimerRow

            now = event.timestamp
            ctx.timers.add_timer(
                TimerRow(
                    name="Hello from a plugin",
                    group=TIMER_GROUP,
                    updated_at=now,
                    ends_at=now + timedelta(seconds=TIMER_SECONDS),
                    total_duration_s=float(TIMER_SECONDS),
                )
            )
            ctx.speaker.speak("Hello from your plugin")

        ctx.subscribe(CommsEvent, on_comms)
        ctx.logger.info("hello-timer ready — say 'hello nparse' in game")


def create_plugin() -> HelloTimerPlugin:
    return HelloTimerPlugin()
