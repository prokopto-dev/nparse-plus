from nparseplus.core.enums import CommsChannel
from nparseplus.core.events import CommsEvent
from nparseplus.core.parsers.comms import CommsParser


def test_group(ctx, make_line, spy):
    events = spy(CommsEvent)
    parser = CommsParser()
    line = make_line("Jaloy tells the group, 'wiki says he can be in 1 of 2 locations'")
    assert parser.handle(line, ctx)
    assert events[0].channel == CommsChannel.GROUP
    assert events[0].sender == "Jaloy"
    assert events[0].content == "wiki says he can be in 1 of 2 locations"


def test_guild(ctx, make_line, spy):
    events = spy(CommsEvent)
    parser = CommsParser()
    assert parser.handle(make_line("You say to your guild, 'nice'"), ctx)
    assert events[0].channel == CommsChannel.GUILD
    assert events[0].sender == "You"


def test_tell(ctx, make_line, spy):
    events = spy(CommsEvent)
    parser = CommsParser()
    assert parser.handle(make_line("Thalistair tells you, 'omw'"), ctx)
    assert events[0].channel == CommsChannel.TELL
    assert events[0].sender == "Thalistair"
    assert events[0].receiver == "you"
    assert events[0].content == "omw"


def test_internal_tell_maps_active_player_to_you(ctx, make_line, spy):
    events = spy(CommsEvent)
    ctx.player.name = "Azleep"
    parser = CommsParser()
    assert parser.handle(make_line("Azleep -> Jamori: ok"), ctx)
    assert events[0].channel == CommsChannel.TELL
    assert events[0].sender == "You"
    assert events[0].receiver == "Jamori"
    assert events[0].content == "ok"


def test_auction(ctx, make_line, spy):
    events = spy(CommsEvent)
    parser = CommsParser()
    assert parser.handle(make_line("Mezzter auctions, 'WTS bone chips 7p per stack pst'"), ctx)
    assert events[0].channel == CommsChannel.AUCTION
    assert events[0].sender == "Mezzter"


def test_say_and_shout_and_ooc(ctx, make_line, spy):
    events = spy(CommsEvent)
    parser = CommsParser()
    assert parser.handle(make_line("Jaloy says, 'i am a new warrior'"), ctx)
    assert events[-1].channel == CommsChannel.SAY
    assert parser.handle(make_line("Enudara says out of character, 'grats'"), ctx)
    assert events[-1].channel == CommsChannel.OOC
    assert parser.handle(
        make_line("Fizzix shouts, 'ASSIST Fizzix on --- [ an essence tamer ]'"), ctx
    )
    assert events[-1].channel == CommsChannel.SHOUT
