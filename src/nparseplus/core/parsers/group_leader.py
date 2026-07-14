"""Group leader tracking parser (port of EQTool GroupLeaderParser.cs).

Like the C# original, every check runs and a later match overwrites an
earlier one (last match wins).
"""

from __future__ import annotations

import re

from nparseplus.core.events import GroupLeaderEvent
from nparseplus.core.lineinfo import LineInfo
from nparseplus.core.parsers.base import ParseContext

_YOU_JOIN_RE = re.compile(
    r"^You notify (?P<group_leader>[\w` ]+) that you agree to join the group."
)
_YOU_INVITE_RE = re.compile(r"^You invite [\w` ]+ to join your group.")
_LEADER_CHANGED_RE = re.compile(r"(?P<group_leader>[\w` ]+) (is|are) now the leader of your group.")


class GroupLeaderParser:
    def handle(self, line: LineInfo, ctx: ParseContext) -> bool:
        message = line.message
        if "group" not in message:
            return False

        leader: str | None = None

        match = _YOU_JOIN_RE.match(message)
        if match:
            leader = match.group("group_leader")

        match = _YOU_INVITE_RE.match(message)
        if match:
            leader = "You"

        match = _LEADER_CHANGED_RE.search(message)
        if match:
            leader = match.group("group_leader")

        if message in ("Your group has been disbanded.", "You have been removed from the group."):
            leader = "None"

        if leader is None:
            return False
        ctx.bus.publish(
            GroupLeaderEvent(
                timestamp=line.timestamp,
                line=message,
                line_number=line.line_number,
                group_leader_name=leader,
            )
        )
        return True
