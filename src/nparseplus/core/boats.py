"""Boat schedule helpers over the zone database's boat table."""

from __future__ import annotations

from datetime import datetime, timedelta

from pydantic import BaseModel, ConfigDict

from nparseplus.core.zones import BoatInfo


class BoatSighting(BaseModel):
    """A boat's projected arrival, derived from a start announcement."""

    model_config = ConfigDict(frozen=True)

    boat: BoatInfo
    announced_at: datetime

    @property
    def docks_at(self) -> datetime:
        return self.announced_at + timedelta(seconds=self.boat.announcement_to_dock_in_seconds)

    @property
    def next_departure_dock_at(self) -> datetime:
        """One full round trip after this docking."""
        return self.docks_at + timedelta(seconds=self.boat.trip_time_in_seconds)
