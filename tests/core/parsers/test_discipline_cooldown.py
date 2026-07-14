from nparseplus.core.events import DisciplineCooldownEvent
from nparseplus.core.parsers.discipline_cooldown import DisciplineCooldownParser


def test_cooldown(ctx, make_line, spy):
    events = spy(DisciplineCooldownEvent)
    parser = DisciplineCooldownParser()
    line = make_line(
        "You can use the ability Puretone Discipline again in 48 minute(s) 45 seconds."
    )
    assert parser.handle(line, ctx)
    assert events[0].discipline_name == "Puretone Discipline"
    assert events[0].total_timer_seconds == 48 * 60 + 45


def test_zero_minutes(ctx, make_line, spy):
    events = spy(DisciplineCooldownEvent)
    parser = DisciplineCooldownParser()
    line = make_line("You can use the ability Defensive Discipline again in 0 minute(s) 9 seconds.")
    assert parser.handle(line, ctx)
    assert events[0].total_timer_seconds == 9


def test_non_cooldown_line(ctx, make_line, spy):
    events = spy(DisciplineCooldownEvent)
    parser = DisciplineCooldownParser()
    assert not parser.handle(make_line("You can use the ability now."), ctx)
    assert events == []
