"""Wire DTO round-trips: camelCase in (live hub/REST), PascalCase out (EQTool)."""

from datetime import UTC, datetime, timedelta

from nparseplus.core.geometry import Loc
from nparseplus.net.pigparse_models import (
    BoatActivity,
    ItemPrice,
    RollTimer,
    WireCustomTimer,
    WireDragonRoar,
    WirePlayer,
    wire_player_from_loc,
)

# Verbatim inbound frame payload from tools/pigparse_probe_transcript.md.
CAMEL_PLAYER = {
    "name": "NparseProbeB",
    "trackingDistance": None,
    "guildName": None,
    "sharing": 0,
    "server": 0,
    "zone": "probezone",
    "x": 0,
    "y": 0,
    "z": 0,
    "groupName": "Green_probezone",  # computed server-side; must be ignored
}

# The same player as EQTool's client would serialize it.
PASCAL_PLAYER = {
    "Name": "NparseProbeB",
    "TrackingDistance": None,
    "GuildName": None,
    "Sharing": 0,
    "Server": 0,
    "Zone": "probezone",
    "X": 0,
    "Y": 0,
    "Z": 0,
}


def test_wire_player_accepts_both_casings() -> None:
    assert WirePlayer.model_validate(CAMEL_PLAYER) == WirePlayer.model_validate(PASCAL_PLAYER)


def test_wire_player_dumps_pascal() -> None:
    dumped = WirePlayer.model_validate(CAMEL_PLAYER).wire_dump()
    assert dumped["Name"] == "NparseProbeB"
    assert dumped["Server"] == 0
    assert "name" not in dumped
    assert "groupName" not in dumped and "GroupName" not in dumped


def test_wire_player_to_remote_player_keeps_wire_order() -> None:
    wire = WirePlayer.model_validate({**CAMEL_PLAYER, "x": 1.5, "y": 2.5, "z": 3.5})
    remote = wire.to_remote_player()
    assert (remote.x, remote.y, remote.z) == (1.5, 2.5, 3.5)
    assert remote.name == "NparseProbeB"
    assert remote.server == 0


def test_wire_player_from_loc_swaps_axes() -> None:
    # "Your Location is 111, 222, 3" parses to Loc(x=222, y=111, z=3);
    # EQTool sends the raw printed order: X=111 (first), Y=222 (second).
    loc = Loc(x=222.0, y=111.0, z=3.0)
    wire = wire_player_from_loc(
        name="Soandso", guild_name="", server=0, zone="gfaydark", sharing=0, loc=loc
    )
    assert (wire.x, wire.y, wire.z) == (111.0, 222.0, 3.0)
    assert wire.guild_name is None  # blank guild normalizes to null like C#


def test_dragon_roar_and_custom_timer_casings() -> None:
    roar = WireDragonRoar.model_validate(
        {"spellName": "Dragon Roar", "server": 1, "zone": "permafrost", "sharing": 0}
    )
    assert roar.spell_name == "Dragon Roar"
    assert roar.wire_dump()["SpellName"] == "Dragon Roar"
    timer = WireCustomTimer.model_validate(
        {"name": "Kael Faction Pull In Progress", "durationInSeconds": 90, "server": 0}
    )
    assert timer.duration_in_seconds == 90
    assert timer.spell_name_icon is None


def test_rest_timestamps_become_naive_local() -> None:
    aware = "2026-07-16T14:00:00+00:00"
    boat = BoatActivity.model_validate({"startPoint": "TIMORROUS", "boat": 2, "lastSeen": aware})
    assert boat.last_seen.tzinfo is None
    expected = datetime(2026, 7, 16, 14, 0, tzinfo=UTC).astimezone().replace(tzinfo=None)
    assert boat.last_seen == expected

    roll = RollTimer.model_validate(
        {"rollTimerType": 2, "guess": False, "name": "Quake", "dateTime": aware}
    )
    assert roll.date_time.tzinfo is None
    assert roll.date_time - boat.last_seen == timedelta(0)


def test_item_price_ignores_unknown_columns() -> None:
    item = ItemPrice.model_validate(
        {
            "eQitemId": 1001,
            "itemName": "Rusty Sword",
            "lastWTSSeen": None,
            "totalWTSLast30DaysCount": 4,
            "totalWTSLast30DaysAverage": 12,
            "totalWTBLastYearAverage": 999,  # not modeled; must not raise
        }
    )
    assert item.item_name == "Rusty Sword"
    assert item.total_wts_last_30_days_count == 4
    assert item.last_wts_seen is None
