from nparseplus.core.events import RandomRollEvent
from nparseplus.core.parsers.random import RandomParser


def test_two_line_roll(ctx, make_line, spy):
    events = spy(RandomRollEvent)
    parser = RandomParser()
    # First line stashes the name but is not consumed (C# behavior).
    assert not parser.handle(make_line("**A Magic Die is rolled by Whitewitch."), ctx)
    assert parser.handle(
        make_line(
            "**It could have been any number from 0 to 100, but this time it turned up a 42."
        ),
        ctx,
    )
    assert events[0].player_name == "Whitewitch"
    assert events[0].max_roll == 100
    assert events[0].roll == 42


def test_second_line_without_first_is_ignored(ctx, make_line, spy):
    events = spy(RandomRollEvent)
    parser = RandomParser()
    assert not parser.handle(
        make_line(
            "**It could have been any number from 0 to 333, but this time it turned up a 12."
        ),
        ctx,
    )
    assert events == []


def test_unrelated_line(ctx, make_line, spy):
    events = spy(RandomRollEvent)
    parser = RandomParser()
    assert not parser.handle(make_line("You gain experience!!"), ctx)
    assert events == []
