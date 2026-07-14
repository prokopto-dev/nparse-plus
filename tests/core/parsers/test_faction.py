from nparseplus.core.enums import FactionStatus
from nparseplus.core.events import FactionEvent
from nparseplus.core.parsers.faction import FactionParser


def test_got_better(ctx, make_line, spy):
    events = spy(FactionEvent)
    parser = FactionParser()
    line = make_line("Your faction standing with KazonThule got better.")
    assert parser.handle(line, ctx)
    assert events[0].faction == "KazonThule"
    assert events[0].status == FactionStatus.GOT_BETTER


def test_got_worse(ctx, make_line, spy):
    events = spy(FactionEvent)
    parser = FactionParser()
    line = make_line("Your faction standing with Guards of Qeynos got worse.")
    assert parser.handle(line, ctx)
    assert events[0].faction == "Guards of Qeynos"
    assert events[0].status == FactionStatus.GOT_WORSE


def test_could_not_get_better(ctx, make_line, spy):
    events = spy(FactionEvent)
    parser = FactionParser()
    line = make_line(
        "Your faction standing with Mayong Mistmoore could not possibly get any better."
    )
    assert parser.handle(line, ctx)
    assert events[0].faction == "Mayong Mistmoore"
    assert events[0].status == FactionStatus.COULD_NOT_GET_BETTER


def test_non_faction_line(ctx, make_line, spy):
    events = spy(FactionEvent)
    parser = FactionParser()
    assert not parser.handle(make_line("You gain experience!!"), ctx)
    assert events == []
