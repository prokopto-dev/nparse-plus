import pytest

from nparseplus.core.bus import EventBus
from nparseplus.core.events import BoatEvent
from nparseplus.core.parsers.base import ParseContext
from nparseplus.core.parsers.boat import BoatParser
from nparseplus.core.player import ActivePlayer
from nparseplus.core.zones import load_zone_database


@pytest.fixture
def ctx() -> ParseContext:
    # BoatParser reads the boat table off the shared ZoneDatabase, so override
    # the zone-less parser ctx fixture with one carrying the real zones.json.
    return ParseContext(bus=EventBus(), player=ActivePlayer(), zones=load_zone_database())


def test_barrel_barge(ctx, make_line, spy):
    events = spy(BoatEvent)
    parser = BoatParser()
    line = make_line("Rack Stonebelly shouts, 'Da Barrel Barge will be here soon soon!'")
    assert parser.handle(line, ctx)
    assert events[0].boat == "BarrelBarge"
    assert events[0].start_point == "oasis"


def test_maidens_voyage_from_firiona(ctx, make_line, spy):
    events = spy(BoatEvent)
    parser = BoatParser()
    line = make_line(
        "Glisse Bluesea shouts 'The Maiden's Voyage has departed the outpost at "
        "Firiona Vie. Please be ready to board the shuttles shortly, if you desire "
        "to make the journey to Kunark."
    )
    assert parser.handle(line, ctx)
    assert events[0].boat == "MaidensVoyage"
    assert events[0].start_point == "firiona"


def test_non_boat_shout(ctx, make_line, spy):
    events = spy(BoatEvent)
    parser = BoatParser()
    assert not parser.handle(make_line("Rack Stonebelly shouts, 'Hello there!'"), ctx)
    assert events == []
