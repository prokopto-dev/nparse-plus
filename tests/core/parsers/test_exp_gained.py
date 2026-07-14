from nparseplus.core.events import ExpGainedEvent
from nparseplus.core.parsers.exp_gained import ExpGainedParser


def test_solo_exp(ctx, make_line, spy):
    events = spy(ExpGainedEvent)
    parser = ExpGainedParser()
    assert parser.handle(make_line("You gain experience!!"), ctx)
    assert len(events) == 1


def test_party_exp(ctx, make_line, spy):
    events = spy(ExpGainedEvent)
    parser = ExpGainedParser()
    assert parser.handle(make_line("You gain party experience!!"), ctx)
    assert len(events) == 1


def test_non_exp_line(ctx, make_line, spy):
    events = spy(ExpGainedEvent)
    parser = ExpGainedParser()
    assert not parser.handle(make_line("You gain a level!"), ctx)
    assert events == []
