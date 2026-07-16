from nparseplus.core.events import YouZonedEvent
from nparseplus.core.parsers.you_zoned import YouZonedParser


def test_you_have_entered(ctx, make_line, spy):
    events = spy(YouZonedEvent)
    parser = YouZonedParser()
    assert parser.handle(make_line("You have entered The Feerrott."), ctx)
    assert events[0].long_name == "the feerrott"
    # ctx has no zone database: falls back to the squashed long name.
    assert events[0].short_name == "thefeerrott"


def test_you_have_entered_with_zone_database(ctx, make_line, spy):
    from nparseplus.core.zones import load_zone_database

    ctx = ctx.__class__(bus=ctx.bus, player=ctx.player, zones=load_zone_database())
    events = spy(YouZonedEvent)
    parser = YouZonedParser()
    assert parser.handle(make_line("You have entered The Feerrott."), ctx)
    # Zones.TranslateToMapName parity: the EQTool short key, not a squash.
    assert events[0].short_name == "feerrott"


def test_there_are_players_in(ctx, make_line, spy):
    events = spy(YouZonedEvent)
    parser = YouZonedParser()
    assert parser.handle(make_line("There are 5 players in East Commonlands."), ctx)
    assert events[0].long_name == "east commonlands"


def test_there_is_player_in(ctx, make_line, spy):
    events = spy(YouZonedEvent)
    parser = YouZonedParser()
    assert parser.handle(make_line("There is 1 player in Kithicor Woods."), ctx)
    assert events[0].long_name == "kithicor woods"


def test_everquest_wide_who_is_ignored(ctx, make_line, spy):
    events = spy(YouZonedEvent)
    parser = YouZonedParser()
    assert not parser.handle(make_line("There are 250 players in EverQuest."), ctx)
    assert events == []


def test_arena_pvp_is_ignored(ctx, make_line, spy):
    events = spy(YouZonedEvent)
    parser = YouZonedParser()
    assert not parser.handle(make_line("You have entered an Arena (PvP) area."), ctx)
    assert events == []
