from nparseplus.core.events import QuakeEvent
from nparseplus.core.parsers.quake import QuakeParser


def test_quake_warning(ctx, make_line, spy):
    events = spy(QuakeEvent)
    parser = QuakeParser()
    line = make_line("You feel you should get somewhere safe as soon as possible.")
    assert parser.handle(line, ctx)
    assert len(events) == 1


def test_gods_awoken(ctx, make_line, spy):
    events = spy(QuakeEvent)
    parser = QuakeParser()
    line = make_line("The gods have awoken to unleash their wrath across Norrath.")
    assert parser.handle(line, ctx)
    assert len(events) == 1


def test_non_quake(ctx, make_line, spy):
    events = spy(QuakeEvent)
    parser = QuakeParser()
    assert not parser.handle(make_line("You feel safe."), ctx)
    assert events == []
