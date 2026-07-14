from nparseplus.core.events import PlayerLevelDetectionEvent
from nparseplus.core.parsers.level import PlayerLevelDetectionParser


def test_level_up(ctx, make_line, spy):
    events = spy(PlayerLevelDetectionEvent)
    parser = PlayerLevelDetectionParser()
    assert parser.handle(make_line("You have gained a level! Welcome to level 12!"), ctx)
    assert events[0].player_level == 12


def test_non_level_line(ctx, make_line, spy):
    events = spy(PlayerLevelDetectionEvent)
    parser = PlayerLevelDetectionParser()
    assert not parser.handle(make_line("You gain experience!!"), ctx)
    assert events == []
