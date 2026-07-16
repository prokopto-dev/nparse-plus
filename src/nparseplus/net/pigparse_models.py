"""PigParse wire DTOs (EQToolShared/HubModels + EQToolShared/APIModels).

Casing, confirmed live (see ``tools/pigparse_probe_transcript.md``): the
SignalR hub and the REST API both serialize **camelCase** to us, while
EQTool's own client sends **PascalCase** and both servers bind
case-insensitively. So every field validates from either casing
(``AliasChoices``) and serializes PascalCase (``model_dump(by_alias=True)``),
byte-compatible with EQTool. Enums travel as ints (``core.enums`` wire
ordinals); unknown properties are ignored.

Timestamps from the REST API are tz-aware ``DateTimeOffset`` strings; the
pipeline compares naive local datetimes everywhere, so they are converted on
validation and never leave this module aware.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Annotated, Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator

from nparseplus.core.events import RemotePlayer

if TYPE_CHECKING:
    from nparseplus.core.geometry import Loc


def _wire_alias(pascal: str) -> Any:
    """Field that reads PascalCase or camelCase and writes PascalCase."""
    camel = pascal[0].lower() + pascal[1:]
    return Field(
        validation_alias=AliasChoices(pascal, camel),
        serialization_alias=pascal,
    )


def _naive_local(value: datetime) -> datetime:
    """tz-aware wire timestamp -> naive local (the pipeline-wide convention)."""
    if value.tzinfo is not None:
        return value.astimezone().replace(tzinfo=None)
    return value


def _naive_local_opt(value: datetime | None) -> datetime | None:
    return _naive_local(value) if value is not None else None


class WireModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore", frozen=True)

    def wire_dump(self) -> dict:
        """JSON-ready dict in PascalCase, as EQTool sends it."""
        return self.model_dump(by_alias=True, mode="json")


class WirePlayer(WireModel):
    """SignalrPlayerV2 (EQToolShared/HubModels/SignalrPlayer.cs)."""

    name: Annotated[str, _wire_alias("Name")]
    guild_name: Annotated[str | None, _wire_alias("GuildName")] = None
    sharing: Annotated[int, _wire_alias("Sharing")] = 0  # MapLocationSharing
    server: Annotated[int | None, _wire_alias("Server")] = None
    zone: Annotated[str, _wire_alias("Zone")] = ""
    x: Annotated[float | None, _wire_alias("X")] = None
    y: Annotated[float | None, _wire_alias("Y")] = None
    z: Annotated[float | None, _wire_alias("Z")] = None
    tracking_distance: Annotated[float | None, _wire_alias("TrackingDistance")] = None

    def to_remote_player(self) -> RemotePlayer:
        """The bus-facing shape. Coordinates stay in wire (raw /loc) order."""
        return RemotePlayer(
            name=self.name,
            server=self.server,
            zone=self.zone,
            guild_name=self.guild_name,
            x=self.x or 0.0,
            y=self.y or 0.0,
            z=self.z or 0.0,
            tracking_distance=self.tracking_distance,
        )


def wire_player_from_loc(
    *,
    name: str,
    guild_name: str | None,
    server: int,
    zone: str,
    sharing: int,
    loc: Loc,
    tracking_distance: float | None = None,
) -> WirePlayer:
    """Build the outbound location payload from a parsed ``Loc``.

    THE axis swap: the client logs ``Your Location is <y>, <x>, <z>`` and
    EQTool (LocationParser.cs) keeps that raw order on the wire, while our
    location parser normalizes to ``Loc(x=2nd, y=1st)`` — so wire X is
    ``loc.y`` and wire Y is ``loc.x``. Do not "fix" this without re-running
    the live map-dot calibration.
    """
    return WirePlayer(
        name=name,
        guild_name=guild_name or None,
        sharing=sharing,
        server=server,
        zone=zone,
        x=loc.y,
        y=loc.x,
        z=loc.z,
        tracking_distance=tracking_distance,
    )


class WireDragonRoar(WireModel):
    """SignalRDragonRoar (EQToolShared/HubModels/SignalRDragonRoar.cs)."""

    spell_name: Annotated[str, _wire_alias("SpellName")]
    guild_name: Annotated[str | None, _wire_alias("GuildName")] = None
    sharing: Annotated[int, _wire_alias("Sharing")] = 0
    server: Annotated[int | None, _wire_alias("Server")] = None
    zone: Annotated[str, _wire_alias("Zone")] = ""
    x: Annotated[float | None, _wire_alias("X")] = None
    y: Annotated[float | None, _wire_alias("Y")] = None
    z: Annotated[float | None, _wire_alias("Z")] = None


def wire_dragon_roar_from_loc(
    *,
    spell_name: str,
    guild_name: str | None,
    server: int,
    zone: str,
    sharing: int,
    loc: Loc | None,
) -> WireDragonRoar:
    """Outbound roar payload; same axis swap as ``wire_player_from_loc``
    (EQTool fills X/Y/Z from its LastPlayer, which is raw /loc order)."""
    return WireDragonRoar(
        spell_name=spell_name,
        guild_name=guild_name or None,
        sharing=sharing,
        server=server,
        zone=zone,
        x=loc.y if loc else None,
        y=loc.x if loc else None,
        z=loc.z if loc else None,
    )


class WireCustomTimer(WireModel):
    """SignalrCustomTimer (EQToolShared/HubModels/CustomTimer.cs)."""

    name: Annotated[str, _wire_alias("Name")]
    duration_in_seconds: Annotated[int, _wire_alias("DurationInSeconds")] = 0
    spell_name_icon: Annotated[str | None, _wire_alias("SpellNameIcon")] = None
    server: Annotated[int | None, _wire_alias("Server")] = None


class WirePlayerRecord(WireModel):
    """Player (EQToolShared/APIModels/PlayerControllerModels.cs)."""

    name: Annotated[str, _wire_alias("Name")]
    guild_name: Annotated[str | None, _wire_alias("GuildName")] = None
    server: Annotated[int | None, _wire_alias("Server")] = None
    player_class: Annotated[int | None, _wire_alias("PlayerClass")] = None
    level: Annotated[int | None, _wire_alias("Level")] = None


class BoatActivity(WireModel):
    """BoatActivityResponce (EQToolShared/APIModels/BoatControllerModels.cs)."""

    start_point: Annotated[str, _wire_alias("StartPoint")] = ""
    boat: Annotated[int, _wire_alias("Boat")] = 0
    last_seen: Annotated[datetime, _wire_alias("LastSeen")]

    _naive = field_validator("last_seen")(_naive_local)


class RollTimer(WireModel):
    """RollTimerModel (EQToolShared/APIModels/RollTimerModel.cs)."""

    roll_timer_type: Annotated[int, _wire_alias("RollTimerType")] = 0
    guess: Annotated[bool, _wire_alias("Guess")] = False
    name: Annotated[str, _wire_alias("Name")] = ""
    date_time: Annotated[datetime, _wire_alias("DateTime")]

    _naive = field_validator("date_time")(_naive_local)


class ItemPrice(WireModel):
    """The MobInfo-relevant subset of Item (ItemControllerModels.cs).

    The full record carries a 24-column WTS/WTB stats block; ``extra="ignore"``
    drops what the UI doesn't show.
    """

    eq_item_id: Annotated[int | None, _wire_alias("EQitemId")] = None
    item_name: Annotated[str, _wire_alias("ItemName")] = ""
    last_wts_seen: Annotated[datetime | None, _wire_alias("LastWTSSeen")] = None
    total_wts_auction_count: Annotated[int, _wire_alias("TotalWTSAuctionCount")] = 0
    total_wts_auction_average: Annotated[int, _wire_alias("TotalWTSAuctionAverage")] = 0
    total_wts_last_30_days_count: Annotated[int, _wire_alias("TotalWTSLast30DaysCount")] = 0
    total_wts_last_30_days_average: Annotated[int, _wire_alias("TotalWTSLast30DaysAverage")] = 0
    total_wts_last_90_days_count: Annotated[int, _wire_alias("TotalWTSLast90DaysCount")] = 0
    total_wts_last_90_days_average: Annotated[int, _wire_alias("TotalWTSLast90DaysAverage")] = 0

    _naive = field_validator("last_wts_seen")(_naive_local_opt)
