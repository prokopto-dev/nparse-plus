from nparseplus.core.events import PlayerLocationEvent
from nparseplus.core.parsers.location import LocationParser


def test_loc_is_normalized_to_xyz(ctx, make_line, spy):
    events = spy(PlayerLocationEvent)
    parser = LocationParser()
    # Client prints y, x, z — Loc is normalized to x, y, z.
    assert parser.handle(make_line("Your Location is -561.00, -5043.00, -194.40"), ctx)
    assert events[0].location.x == -5043.0
    assert events[0].location.y == -561.0
    assert events[0].location.z == -194.4


def test_non_location_line(ctx, make_line, spy):
    events = spy(PlayerLocationEvent)
    parser = LocationParser()
    assert not parser.handle(make_line("You land."), ctx)
    assert events == []
