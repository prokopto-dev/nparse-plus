from nparseplus.core.events import ConEvent
from nparseplus.core.parsers.con import ConLogParse


def test_scowls(ctx, make_line, spy):
    events = spy(ConEvent)
    parser = ConLogParse()
    line = make_line(
        "a gnoll scowls at you, ready to attack -- what would you like your tombstone to say?"
    )
    assert parser.handle(line, ctx)
    assert events[0].name == "a gnoll"


def test_indifferent(ctx, make_line, spy):
    events = spy(ConEvent)
    parser = ConLogParse()
    line = make_line(
        "Guard Hobblin regards you indifferently -- what would you like your tombstone to say?"
    )
    assert parser.handle(line, ctx)
    assert events[0].name == "Guard Hobblin"


def test_non_con_line(ctx, make_line, spy):
    events = spy(ConEvent)
    parser = ConLogParse()
    assert not parser.handle(make_line("You gain experience!!"), ctx)
    assert events == []
