from nparseplus.core.events import SlainEvent
from nparseplus.core.parsers.slain import SlainParser


def test_slain_by(ctx, make_line, spy):
    events = spy(SlainEvent)
    parser = SlainParser()
    assert parser.handle(make_line("a Tesch Mas Gnoll has been slain by Genartik!"), ctx)
    assert events[0].victim == "a Tesch Mas Gnoll"
    assert events[0].killer == "Genartik"


def test_you_have_been_slain(ctx, make_line, spy):
    events = spy(SlainEvent)
    parser = SlainParser()
    assert parser.handle(make_line("You have been slain by a brigand!"), ctx)
    assert events[0].victim == "You"
    assert events[0].killer == "a brigand"


def test_you_have_slain(ctx, make_line, spy):
    events = spy(SlainEvent)
    parser = SlainParser()
    assert parser.handle(make_line("You have slain a Tesch Mas Gnoll!"), ctx)
    assert events[0].victim == "a Tesch Mas Gnoll"
    assert events[0].killer == "You"


def test_died(ctx, make_line, spy):
    events = spy(SlainEvent)
    parser = SlainParser()
    assert parser.handle(make_line("a bile golem died."), ctx)
    assert events[0].victim == "a bile golem"
    assert events[0].killer == ""


def test_eye_of_is_ignored(ctx, make_line, spy):
    events = spy(SlainEvent)
    parser = SlainParser()
    assert not parser.handle(make_line("Eye of Zomm has been slain by a gnoll!"), ctx)
    assert events == []
