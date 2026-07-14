from nparseplus.core.events import MendWoundsEvent
from nparseplus.core.parsers.mend_wounds import MendWoundsParser


def test_mend_success_publishes_but_does_not_consume(ctx, make_line, spy):
    events = spy(MendWoundsEvent)
    parser = MendWoundsParser()
    # C# MendWoundsParser always returns false, even when it publishes.
    assert not parser.handle(make_line("You mend your wounds and heal some damage."), ctx)
    assert len(events) == 1


def test_mend_failure(ctx, make_line, spy):
    events = spy(MendWoundsEvent)
    parser = MendWoundsParser()
    assert not parser.handle(make_line("You have failed to mend your wounds."), ctx)
    assert len(events) == 1


def test_non_mend_line(ctx, make_line, spy):
    events = spy(MendWoundsEvent)
    parser = MendWoundsParser()
    assert not parser.handle(make_line("You mend quickly."), ctx)
    assert events == []
