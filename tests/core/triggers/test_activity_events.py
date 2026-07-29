"""TriggerFiredEvent — the activity-log feed behind the Trigger Editor's
Activity tab (#31). Qt-free: this is all core engine behavior."""

from datetime import UTC, datetime, timedelta

from tests._helpers import FakeSpeaker

from nparseplus.core.bus import EventBus
from nparseplus.core.events import LineEvent, TriggerFiredEvent
from nparseplus.core.player import ActivePlayer
from nparseplus.core.triggers.engine import TriggerEngine
from nparseplus.core.triggers.model import (
    EndEarlyEntry,
    TimerType,
    Trigger,
    TriggerAudioType,
    TriggerOutput,
    TriggerTimer,
    TriggerTimerEnded,
    TriggerTimerEnding,
    trigger_group_key,
)

T0 = datetime(2026, 7, 14, 12, 0, 0, tzinfo=UTC)


class FakeTimers:
    def __init__(self) -> None:
        self.added: list[tuple[str, int, str, str, str]] = []
        self.cancelled: list[str] = []

    def add_timer(self, name: str, seconds: int, color: str, icon: str, restart: str) -> None:
        self.added.append((name, seconds, color, icon, restart))

    def cancel(self, name: str) -> None:
        self.cancelled.append(name)


class Clock:
    def __init__(self, now: datetime = T0) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> datetime:
        self.now += timedelta(seconds=seconds)
        return self.now


def make_engine(
    *triggers: Trigger, zone: str = "", name: str = "Gandalf"
) -> tuple[TriggerEngine, EventBus, Clock, list[TriggerFiredEvent]]:
    bus = EventBus()
    engine = TriggerEngine(
        bus=bus,
        player=ActivePlayer(name=name, zone=zone),
        speaker=FakeSpeaker(),
        timers=FakeTimers(),
        clock=Clock(),
    )
    engine.set_triggers(list(triggers))
    fired: list[TriggerFiredEvent] = []
    bus.subscribe(TriggerFiredEvent, fired.append)
    return engine, bus, engine.clock, fired


def push(bus: EventBus, line: str, line_number: int = 7) -> None:
    bus.publish(LineEvent(timestamp=T0, line=line, line_number=line_number))


def make_trigger(**kwargs) -> Trigger:
    kwargs.setdefault("trigger_enabled", True)
    return Trigger(**kwargs)


# -- the match phase -----------------------------------------------------------


def test_match_publishes_identity_and_source_line() -> None:
    trigger = make_trigger(
        trigger_name="Rampage",
        category="Raid Pack / Sebilis",
        search_text="^{mob} goes on a rampage",
    )
    _, bus, _, fired = make_engine(trigger)
    push(bus, "Trakanon goes on a rampage!", line_number=42)

    assert len(fired) == 1
    event = fired[0]
    assert event.trigger_id == trigger.trigger_id
    assert event.trigger_name == "Rampage"
    assert event.phase == "match"
    # The full nested GINA folder path — what the user recognises.
    assert event.group == "Raid Pack / Sebilis"
    assert event.line == "Trakanon goes on a rampage!"
    assert event.line_number == 42
    assert event.timestamp == T0


def test_match_carries_expanded_outputs() -> None:
    trigger = make_trigger(
        trigger_name="Backstabber",
        search_text="^{who} backstabs {target} for {damage} points",
        basic=TriggerOutput(
            display_text_enabled=True,
            display_text="{who} hit {target} for {damage} (#{COUNTER})",
            audio_type=TriggerAudioType.TEXT_TO_SPEECH,
            tts_text="Backstab on {c}",
        ),
    )
    _, bus, _, fired = make_engine(trigger, name="Gandalf")
    push(bus, "Roger backstabs a rabbit for 1000 points of damage.")

    assert fired[0].display_text == "Roger hit a rabbit for 1000 (#1)"
    assert fired[0].tts_text == "Backstab on Gandalf"
    assert fired[0].counter == 1


def test_silent_trigger_still_publishes() -> None:
    """A trigger with no outputs is exactly the one being hunted, so it must
    still show up in the activity log."""
    trigger = make_trigger(trigger_name="Quiet", search_text="^nothing happens")
    _, bus, _, fired = make_engine(trigger)
    push(bus, "nothing happens at all")

    assert len(fired) == 1
    assert fired[0].display_text == ""
    assert fired[0].tts_text == ""
    assert fired[0].timer_name == ""


def test_sound_file_only_reported_when_a_player_is_wired() -> None:
    output = TriggerOutput(audio_type=TriggerAudioType.SOUND_FILE, sound_file="alarm.wav")
    trigger = make_trigger(trigger_name="Alarm", search_text="^wake up", basic=output)
    engine, bus, _, fired = make_engine(trigger)
    push(bus, "wake up")
    assert fired[0].sound_file == ""  # no sound_player configured

    played: list[str] = []
    engine.sound_player = played.append
    push(bus, "wake up")
    assert fired[1].sound_file == "alarm.wav"
    assert played == ["alarm.wav"]


def test_counter_reports_running_tally() -> None:
    trigger = make_trigger(trigger_name="Tick", search_text="^tick")
    _, bus, _, fired = make_engine(trigger)
    push(bus, "tick")
    push(bus, "tick")

    assert [event.counter for event in fired] == [1, 2]


def test_first_match_wins_publishes_one_event() -> None:
    first = make_trigger(trigger_name="First", search_text="^shared line")
    second = make_trigger(trigger_name="Second", search_text="^shared line")
    _, bus, _, fired = make_engine(first, second)
    push(bus, "shared line here")

    assert [event.trigger_name for event in fired] == ["First"]


def test_gated_triggers_publish_nothing() -> None:
    disabled = Trigger(trigger_enabled=False, trigger_name="Off", search_text="^gate me")
    wrong_zone = make_trigger(trigger_name="Zoned", search_text="^gate me", zone="soldungb")
    wrong_char = make_trigger(trigger_name="Scoped", search_text="^gate me", characters=["Frodo"])
    _, bus, _, fired = make_engine(disabled, wrong_zone, wrong_char, zone="kael", name="Gandalf")
    push(bus, "gate me please")

    assert fired == []


def test_no_match_publishes_nothing() -> None:
    trigger = make_trigger(trigger_name="Rampage", search_text="^rampage")
    _, bus, _, fired = make_engine(trigger)
    push(bus, "a gnoll hits you for 12 points of damage.")

    assert fired == []


def test_closed_engine_publishes_nothing() -> None:
    trigger = make_trigger(trigger_name="Rampage", search_text="^rampage")
    engine, bus, _, fired = make_engine(trigger)
    engine.close()
    push(bus, "rampage!")

    assert fired == []


# -- timers --------------------------------------------------------------------


def _timer_trigger() -> Trigger:
    return make_trigger(
        trigger_name="Rampage",
        built_in_folder="Encounters",
        is_built_in=True,
        search_text="^{mob} goes on a rampage",
        timer=TriggerTimer(timer_type=TimerType.COUNT_DOWN, timer_name="{mob} rampage", seconds=30),
        timer_ending=TriggerTimerEnding(
            enabled=True,
            seconds=5,
            output=TriggerOutput(display_text_enabled=True, display_text="Rampage soon"),
        ),
        timer_ended=TriggerTimerEnded(
            enabled=True,
            output=TriggerOutput(display_text_enabled=True, display_text="Rampage over"),
        ),
    )


def test_match_reports_the_timer_it_armed() -> None:
    _, bus, _, fired = make_engine(_timer_trigger())
    push(bus, "Trakanon goes on a rampage!")

    assert fired[0].timer_name == "Trakanon rampage"
    assert fired[0].timer_seconds == 30
    # Built-ins are filed under their shipped folder.
    assert fired[0].group == "Encounters"


def test_tick_publishes_timer_ending_then_ended_with_the_source_line() -> None:
    engine, bus, clock, fired = make_engine(_timer_trigger())
    push(bus, "Trakanon goes on a rampage!", line_number=11)
    fired.clear()

    engine.tick(clock.advance(26))
    assert [event.phase for event in fired] == ["timer_ending"]
    assert fired[0].display_text == "Rampage soon"
    # The tick has no line of its own — it replays the line that armed it.
    assert fired[0].line == "Trakanon goes on a rampage!"
    assert fired[0].line_number == 11
    assert fired[0].timer_name == "Trakanon rampage"

    engine.tick(clock.advance(10))
    assert [event.phase for event in fired] == ["timer_ending", "timer_ended"]
    assert fired[1].display_text == "Rampage over"

    # Ending fires once, not on every subsequent tick.
    fired.clear()
    engine.tick(clock.advance(5))
    assert fired == []


def test_end_early_publishes_a_cancellation_with_the_cancelling_line() -> None:
    trigger = _timer_trigger()
    assert trigger.timer is not None
    trigger.timer.end_early_texts = [EndEarlyEntry(search_text="Trakanon has been slain")]
    _, bus, _, fired = make_engine(trigger)
    push(bus, "Trakanon goes on a rampage!")
    fired.clear()

    push(bus, "Trakanon has been slain by Gandalf!", line_number=99)

    assert [event.phase for event in fired] == ["timer_cancelled"]
    assert fired[0].timer_name == "Trakanon rampage"
    assert fired[0].line == "Trakanon has been slain by Gandalf!"
    assert fired[0].line_number == 99


# -- the shared group rule -----------------------------------------------------


def test_trigger_group_key_matches_the_editor_tree() -> None:
    builtin = Trigger(is_built_in=True, built_in_folder="Encounters")
    assert trigger_group_key(builtin) == "Encounters"
    assert trigger_group_key(Trigger(is_built_in=True)) == "Built-in"
    assert trigger_group_key(Trigger(category="")) == "Custom"
    assert trigger_group_key(Trigger(category="Default")) == "Custom"
    assert trigger_group_key(Trigger(category="  ")) == "Custom"
    assert trigger_group_key(Trigger(category="Raid Pack / Sebilis")) == "Raid Pack / Sebilis"
