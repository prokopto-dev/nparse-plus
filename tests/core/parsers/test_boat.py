from nparseplus.core.events import BoatEvent
from nparseplus.core.parsers.boat import BoatParser


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
