from nparseplus.core.events import RingWarEvent
from nparseplus.core.parsers.ring_war import RingWarParser


def test_ring_war_start(ctx, make_line, spy):
    events = spy(RingWarEvent)
    parser = RingWarParser()
    line = make_line("Seneschal Aldikar shouts, TROOPS, TAKE YOUR POSITIONS!")
    assert parser.handle(line, ctx)
    assert len(events) == 1


def test_other_shout(ctx, make_line, spy):
    events = spy(RingWarEvent)
    parser = RingWarParser()
    assert not parser.handle(make_line("Seneschal Aldikar shouts, 'Hold the line!'"), ctx)
    assert events == []
