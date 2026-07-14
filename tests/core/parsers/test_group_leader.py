from nparseplus.core.events import GroupLeaderEvent
from nparseplus.core.parsers.group_leader import GroupLeaderParser


def test_you_join(ctx, make_line, spy):
    events = spy(GroupLeaderEvent)
    parser = GroupLeaderParser()
    line = make_line("You notify Wreckeder that you agree to join the group.")
    assert parser.handle(line, ctx)
    assert events[0].group_leader_name == "Wreckeder"


def test_you_invite(ctx, make_line, spy):
    events = spy(GroupLeaderEvent)
    parser = GroupLeaderParser()
    assert parser.handle(make_line("You invite Wreckeder to join your group."), ctx)
    assert events[0].group_leader_name == "You"


def test_leader_changed(ctx, make_line, spy):
    events = spy(GroupLeaderEvent)
    parser = GroupLeaderParser()
    assert parser.handle(make_line("Wreckeder is now the leader of your group."), ctx)
    assert events[0].group_leader_name == "Wreckeder"


def test_disband(ctx, make_line, spy):
    events = spy(GroupLeaderEvent)
    parser = GroupLeaderParser()
    assert parser.handle(make_line("Your group has been disbanded."), ctx)
    assert events[0].group_leader_name == "None"
    assert parser.handle(make_line("You have been removed from the group."), ctx)
    assert events[1].group_leader_name == "None"


def test_non_group_line(ctx, make_line, spy):
    events = spy(GroupLeaderEvent)
    parser = GroupLeaderParser()
    assert not parser.handle(make_line("You gain experience!!"), ctx)
    assert events == []
