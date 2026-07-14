from nparseplus.core.events import FTEEvent
from nparseplus.core.parsers.fte import FTEParser


def test_fte(ctx, make_line, spy):
    events = spy(FTEEvent)
    parser = FTEParser()
    assert parser.handle(make_line("Lord Nagafen engages Whitewitch!"), ctx)
    assert events[0].npc_name == "Lord Nagafen"
    assert events[0].fte_person == "Whitewitch"


def test_multiword_engager_is_rejected(ctx, make_line, spy):
    events = spy(FTEEvent)
    parser = FTEParser()
    assert not parser.handle(make_line("Lord Nagafen engages two players!"), ctx)
    assert events == []


def test_no_exclamation(ctx, make_line, spy):
    events = spy(FTEEvent)
    parser = FTEParser()
    assert not parser.handle(make_line("Lord Nagafen engages Whitewitch."), ctx)
    assert events == []
