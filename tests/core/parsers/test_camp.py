import time

from nparseplus.core.events import CampEvent
from nparseplus.core.parsers.camp import CampParser


def _wait_for_timer(parser: CampParser) -> None:
    timer = parser._timer
    assert timer is not None
    timer.join(timeout=2)


def test_camp_fires_after_delay(ctx, make_line, spy):
    events = spy(CampEvent)
    parser = CampParser()
    parser.camp_delay_seconds = 0.01
    assert parser.handle(make_line("It will take about 5 more seconds to prepare your camp."), ctx)
    _wait_for_timer(parser)
    assert len(events) == 1


def test_abandon_cancels_camp(ctx, make_line, spy):
    events = spy(CampEvent)
    parser = CampParser()
    parser.camp_delay_seconds = 0.05
    assert parser.handle(make_line("It will take about 5 more seconds to prepare your camp."), ctx)
    assert parser.handle(make_line("You abandon your preparations to camp."), ctx)
    _wait_for_timer(parser)
    time.sleep(0.01)
    assert events == []


def test_non_camp_line(ctx, make_line, spy):
    events = spy(CampEvent)
    parser = CampParser()
    assert not parser.handle(make_line("You gain experience!!"), ctx)
    assert events == []
