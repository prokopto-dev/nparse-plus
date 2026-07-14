"""Player communications parser (port of EQTool CommsParser.cs).

Channels are checked in the C# order: group, guild, internal tell,
"is not online" (a tell), say, tell, auction, ooc, shout.
"""

from __future__ import annotations

import re

from nparseplus.core.enums import CommsChannel
from nparseplus.core.events import CommsEvent
from nparseplus.core.lineinfo import LineInfo
from nparseplus.core.parsers.base import ParseContext

_PARTY_RE = re.compile(r"^(?P<sender>.+) ((tell your party)|(tells the group)), '(?P<content>.+)'")
_GUILD_RE = re.compile(r"^(?P<sender>.+) ((say to your)|(tells the)) guild, '(?P<content>.+)'")
_INTERNAL_TELL_RE = re.compile(r"^(?P<sender>.+) -> (?P<receiver>.+): (?P<content>.+)")
_IS_NOT_ONLINE_RE = re.compile(r"^(?P<content>.+) is not online at this time.")
_SAYS_RE = re.compile(r"^(?P<sender>.+) (say|says),? '(?P<content>.+)'")
_TOLD_RE = re.compile(r"^(?P<sender>.+) (told|tells) (?P<receiver>.+), '(?P<content>.+)'")
_AUCTIONS_RE = re.compile(r"^(?P<sender>.+) auction(s)?, '(?P<content>.+)'")
_OOC_RE = re.compile(r"^(?P<sender>.+) say(s)? out of character, '(?P<content>.+)'")
_SHOUTS_RE = re.compile(r"^(?P<sender>.+) shout(s)?, '(?P<content>.+)'")


class CommsParser:
    def handle(self, line: LineInfo, ctx: ParseContext) -> bool:
        message = line.message
        # Every comms pattern needs a quoted body, an "A -> B:" tell, or the
        # offline-tell notice.
        if (
            "'" not in message
            and " -> " not in message
            and " is not online at this time" not in message
        ):
            return False

        event = self._match(message, ctx)
        if event is None:
            return False
        ctx.bus.publish(
            CommsEvent(
                timestamp=line.timestamp,
                line=message,
                line_number=line.line_number,
                channel=event[0],
                content=event[1],
                sender=event[2],
                receiver=event[3],
            )
        )
        return True

    @staticmethod
    def _match(message: str, ctx: ParseContext) -> tuple[CommsChannel, str, str, str] | None:
        """Return (channel, content, sender, receiver) or None."""
        match = _PARTY_RE.match(message)
        if match:
            return (CommsChannel.GROUP, match.group("content"), match.group("sender"), "")

        match = _GUILD_RE.match(message)
        if match:
            return (CommsChannel.GUILD, match.group("content"), match.group("sender"), "")

        match = _INTERNAL_TELL_RE.match(message)
        if match:
            sender = match.group("sender")
            if sender == ctx.player.name:
                sender = "You"
            receiver = match.group("receiver")
            if receiver == ctx.player.name:
                receiver = "You"
            return (CommsChannel.TELL, match.group("content"), sender, receiver)

        match = _IS_NOT_ONLINE_RE.match(message)
        if match:
            return (CommsChannel.TELL, match.group("content"), "System", "You")

        match = _SAYS_RE.match(message)
        if match:
            return (CommsChannel.SAY, match.group("content"), match.group("sender"), "")

        match = _TOLD_RE.match(message)
        if match:
            return (
                CommsChannel.TELL,
                match.group("content"),
                match.group("sender"),
                match.group("receiver"),
            )

        match = _AUCTIONS_RE.match(message)
        if match:
            return (CommsChannel.AUCTION, match.group("content"), match.group("sender"), "")

        match = _OOC_RE.match(message)
        if match:
            return (CommsChannel.OOC, match.group("content"), match.group("sender"), "")

        match = _SHOUTS_RE.match(message)
        if match:
            return (CommsChannel.SHOUT, match.group("content"), match.group("sender"), "")

        return None
