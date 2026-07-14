from nparseplus.core.events import YourItemBeginsToGlowEvent
from nparseplus.core.parsers.your_item_begins_to_glow import YourItemBeginsToGlowParser


def test_item_glow(ctx, make_line, spy):
    events = spy(YourItemBeginsToGlowEvent)
    parser = YourItemBeginsToGlowParser()
    assert parser.handle(make_line("Your Mask of Deception begins to glow."), ctx)
    assert events[0].item_name == "Mask of Deception"


def test_hand_glow_is_ignored(ctx, make_line, spy):
    events = spy(YourItemBeginsToGlowEvent)
    parser = YourItemBeginsToGlowParser()
    assert not parser.handle(make_line("Your hand begins to glow."), ctx)
    assert events == []


def test_non_glow_line(ctx, make_line, spy):
    events = spy(YourItemBeginsToGlowEvent)
    parser = YourItemBeginsToGlowParser()
    assert not parser.handle(make_line("Your faction standing with Bloodsabers got worse."), ctx)
    assert events == []
