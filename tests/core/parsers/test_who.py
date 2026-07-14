from nparseplus.core.enums import PlayerClass
from nparseplus.core.events import WhoEvent, WhoPlayerEvent
from nparseplus.core.parsers.who import PlayerWhoLogParse, parse_player_info


def test_who_block(ctx, make_line, spy):
    who_events = spy(WhoEvent)
    player_events = spy(WhoPlayerEvent)
    parser = PlayerWhoLogParse()
    assert parser.handle(make_line("Players on EverQuest:"), ctx)
    assert len(who_events) == 1
    assert not parser.handle(make_line("---------------------------"), ctx)
    assert parser.handle(make_line("[60 High Priest] Dany (High Elf) <The Drift>"), ctx)
    assert player_events[0].player.name == "Dany"
    assert player_events[0].player.level == 60
    assert player_events[0].player.player_class == PlayerClass.CLERIC
    assert player_events[0].player.guild_name == "The Drift"


def test_player_row_outside_block_is_ignored(ctx, make_line, spy):
    player_events = spy(WhoPlayerEvent)
    parser = PlayerWhoLogParse()
    assert not parser.handle(make_line("[60 High Priest] Dany (High Elf) <The Drift>"), ctx)
    assert player_events == []


def test_parse_anon_player():
    player = parse_player_info("[ANONYMOUS] Rundorig  <The Drift>")
    assert player is not None
    assert player.name == "Rundorig"
    assert player.guild_name == "The Drift"
    assert player.level is None
    assert player.player_class is None


def test_parse_afk_anon_player():
    player = parse_player_info("AFK [ANONYMOUS] Rundorig  <The Drift>")
    assert player is not None
    assert player.name == "Rundorig"


def test_parse_warrior_titles():
    player = parse_player_info("[58 Myrmidon] Bdain (Barbarian) <The Drift> LFG")
    assert player is not None
    assert player.name == "Bdain"
    assert player.level == 58
    assert player.player_class == PlayerClass.WARRIOR


def test_guild_motd_is_not_a_player():
    assert parse_player_info("GUILD MOTD: <<>> things go here [ test ] ") is None
