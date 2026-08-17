from datetime import datetime, timedelta

from nparseplus.core.events import CampEvent
from nparseplus.core.parsers.camp import CampParser

T0 = datetime(2026, 7, 15, 12, 0, 0)

CAMP_LINE = "It will take about 5 more seconds to prepare your camp."
ABANDON_LINE = "You abandon your preparations to camp."


def _parser() -> CampParser:
    return CampParser(clock=lambda: T0)


def test_camp_fires_once_the_delay_has_passed(ctx, make_line, spy):
    events = spy(CampEvent)
    parser = _parser()
    assert parser.handle(make_line(CAMP_LINE), ctx)
    # Nothing before the deadline: the 6 s delay is unchanged, only its thread.
    parser.tick(T0 + timedelta(seconds=5.9))
    assert events == []
    parser.tick(T0 + timedelta(seconds=6))
    assert len(events) == 1
    # ...and exactly once.
    parser.tick(T0 + timedelta(seconds=30))
    assert len(events) == 1


def test_camp_event_carries_the_lines_own_timestamp(ctx, make_line, spy):
    events = spy(CampEvent)
    parser = _parser()
    line = make_line(CAMP_LINE)
    parser.handle(line, ctx)
    parser.tick(T0 + timedelta(seconds=6))
    assert events[0].timestamp == line.timestamp
    assert events[0].line == CAMP_LINE


def test_abandon_cancels_camp(ctx, make_line, spy):
    events = spy(CampEvent)
    parser = _parser()
    assert parser.handle(make_line(CAMP_LINE), ctx)
    assert parser.handle(make_line(ABANDON_LINE), ctx)
    parser.tick(T0 + timedelta(seconds=60))
    assert events == []


def test_camping_again_after_abandon_still_fires(ctx, make_line, spy):
    events = spy(CampEvent)
    parser = _parser()
    parser.handle(make_line(CAMP_LINE), ctx)
    parser.handle(make_line(ABANDON_LINE), ctx)
    parser.handle(make_line(CAMP_LINE), ctx)
    parser.tick(T0 + timedelta(seconds=6))
    assert len(events) == 1


def test_tick_without_a_pending_camp_is_a_noop(ctx, spy):
    events = spy(CampEvent)
    _parser().tick(T0 + timedelta(hours=1))
    assert events == []


def test_non_camp_line(ctx, make_line, spy):
    events = spy(CampEvent)
    parser = _parser()
    assert not parser.handle(make_line("You gain experience!!"), ctx)
    assert events == []


def test_camp_event_is_published_on_the_ticking_thread(ctx, make_line):
    """The whole point of #120's step 1: no timer thread publishes CampEvent.

    TimersService is not thread-safe and only the driver tick may touch it, so
    a subscriber must see the event on the thread that called ``tick``.
    """
    import threading

    seen: list[str] = []
    ctx.bus.subscribe(CampEvent, lambda _e: seen.append(threading.current_thread().name))
    parser = CampParser(clock=lambda: T0)
    parser.handle(make_line(CAMP_LINE), ctx)

    ticker = threading.Thread(target=parser.tick, args=(T0 + timedelta(seconds=6),), name="ticker")
    ticker.start()
    ticker.join(timeout=2)
    assert seen == ["ticker"]
