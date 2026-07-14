from nparseplus.core.events import WelcomeEvent
from nparseplus.core.parsers.welcome import WelcomeParser


def test_welcome(ctx, make_line, spy):
    events = spy(WelcomeEvent)
    parser = WelcomeParser()
    assert parser.handle(make_line("Welcome to EverQuest!"), ctx)
    assert len(events) == 1


def test_non_welcome(ctx, make_line, spy):
    events = spy(WelcomeEvent)
    parser = WelcomeParser()
    assert not parser.handle(make_line("Welcome to Norrath!"), ctx)
    assert events == []
