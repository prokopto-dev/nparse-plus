"""End-to-end TriggerEngine behavior with fake speaker/timer sinks."""

from datetime import UTC, datetime, timedelta

from tests._helpers import FakeSpeaker

from nparseplus.core.bus import EventBus
from nparseplus.core.events import LineEvent, OverlayEvent, TimerBarEvent
from nparseplus.core.player import ActivePlayer
from nparseplus.core.triggers.engine import TriggerEngine
from nparseplus.core.triggers.model import (
    EndEarlyEntry,
    TimerRestartBehavior,
    TimerType,
    Trigger,
    TriggerAudioType,
    TriggerCounter,
    TriggerOutput,
    TriggerTimer,
    TriggerTimerEnded,
    TriggerTimerEnding,
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
) -> tuple[TriggerEngine, EventBus, FakeSpeaker, FakeTimers, Clock, list]:
    bus = EventBus()
    player = ActivePlayer(name=name, zone=zone)
    speaker = FakeSpeaker()
    timers = FakeTimers()
    clock = Clock()
    engine = TriggerEngine(bus=bus, player=player, speaker=speaker, timers=timers, clock=clock)
    engine.set_triggers(list(triggers))
    published: list = []
    bus.subscribe(OverlayEvent, published.append)
    bus.subscribe(TimerBarEvent, published.append)
    return engine, bus, speaker, timers, clock, published


def push(bus: EventBus, line: str) -> None:
    bus.publish(LineEvent(timestamp=T0, line=line, line_number=1))


def test_basic_output_tts_and_overlay_with_captures() -> None:
    trigger = Trigger(
        trigger_enabled=True,
        trigger_name="Backstabber",
        search_text="^{backstabber} backstabs {target} for {damage} points of damage",
        basic=TriggerOutput(
            display_text_enabled=True,
            display_text="{backstabber} hit {target} for {damage}",
            display_text_color="Orange",
            audio_type=TriggerAudioType.TEXT_TO_SPEECH,
            tts_text="Backstab for {damage}",
        ),
    )
    _, bus, speaker, _, _, published = make_engine(trigger)
    push(bus, "Roger the Rogue backstabs a poor rabbit for 1000 points of damage.")

    assert speaker.spoken == ["Backstab for 1000"]
    overlay = [e for e in published if isinstance(e, OverlayEvent)]
    assert len(overlay) == 1
    assert overlay[0].text == "Roger the Rogue hit a poor rabbit for 1000"
    assert overlay[0].foreground == "Orange"
    assert not overlay[0].reset


def _tts_trigger(*, interrupt: bool) -> Trigger:
    return Trigger(
        trigger_enabled=True,
        search_text="pull now",
        use_regex=False,
        basic=TriggerOutput(
            audio_type=TriggerAudioType.TEXT_TO_SPEECH,
            tts_text="pull",
            interrupt_speech=interrupt,
        ),
    )


def test_interrupt_speech_flag_interrupts_before_speaking() -> None:
    _, bus, speaker, _, _, _ = make_engine(_tts_trigger(interrupt=True))
    push(bus, "pull now")
    assert speaker.spoken == ["pull"]
    assert speaker.interrupts == 1


def test_tts_without_interrupt_flag_does_not_interrupt() -> None:
    _, bus, speaker, _, _, _ = make_engine(_tts_trigger(interrupt=False))
    push(bus, "pull now")
    assert speaker.spoken == ["pull"]
    assert speaker.interrupts == 0


def test_overlay_text_resets_after_five_seconds_via_tick() -> None:
    trigger = Trigger(
        trigger_enabled=True,
        search_text="hello",
        use_regex=False,
        basic=TriggerOutput(display_text_enabled=True, display_text="HI"),
    )
    engine, bus, _, _, clock, published = make_engine(trigger)
    push(bus, "hello there")
    engine.tick(clock.advance(4.9))
    assert len([e for e in published if isinstance(e, OverlayEvent)]) == 1
    engine.tick(clock.advance(0.2))
    overlay = [e for e in published if isinstance(e, OverlayEvent)]
    assert len(overlay) == 2
    assert overlay[1].reset


def test_disabled_trigger_and_zone_gate() -> None:
    disabled = Trigger(
        trigger_enabled=False,
        search_text="hello",
        use_regex=False,
        basic=TriggerOutput(audio_type=TriggerAudioType.TEXT_TO_SPEECH, tts_text="nope"),
    )
    zoned = Trigger(
        trigger_enabled=True,
        search_text="hello",
        use_regex=False,
        zone="kael",
        basic=TriggerOutput(audio_type=TriggerAudioType.TEXT_TO_SPEECH, tts_text="kael only"),
    )
    _, bus, speaker, _, _, _ = make_engine(disabled, zoned, zone="fear")
    push(bus, "hello")
    assert speaker.spoken == []


def test_zone_gated_trigger_fires_in_its_zone() -> None:
    zoned = Trigger(
        trigger_enabled=True,
        search_text="wind begins",
        use_regex=False,
        zone="kael",
        basic=TriggerOutput(audio_type=TriggerAudioType.TEXT_TO_SPEECH, tts_text="AOE"),
    )
    _, bus, speaker, _, _, _ = make_engine(zoned, zone="kael")
    push(bus, "The wind begins to blow.")
    assert speaker.spoken == ["AOE"]


def test_first_matching_trigger_consumes_the_line() -> None:
    first = Trigger(
        trigger_enabled=True,
        search_text="hello",
        use_regex=False,
        basic=TriggerOutput(audio_type=TriggerAudioType.TEXT_TO_SPEECH, tts_text="first"),
    )
    second = Trigger(
        trigger_enabled=True,
        search_text="hello",
        use_regex=False,
        basic=TriggerOutput(audio_type=TriggerAudioType.TEXT_TO_SPEECH, tts_text="second"),
    )
    _, bus, speaker, _, _, _ = make_engine(first, second)
    push(bus, "hello world")
    assert speaker.spoken == ["first"]


def test_context_trigger_recompiles_on_set_player() -> None:
    trigger = Trigger(
        trigger_enabled=True,
        search_text="^{c} waves",
        basic=TriggerOutput(audio_type=TriggerAudioType.TEXT_TO_SPEECH, tts_text="wave"),
    )
    engine, bus, speaker, _, _, _ = make_engine(trigger, name="Gandalf")
    push(bus, "Frodo waves")
    assert speaker.spoken == []
    push(bus, "Gandalf waves")
    assert speaker.spoken == ["wave"]

    # character change: engine reads player.name per line, set_player pre-warms
    engine.player.name = "Frodo"
    engine.set_player("Frodo")
    push(bus, "Frodo waves")
    assert speaker.spoken == ["wave", "wave"]
    push(bus, "Gandalf waves")
    assert speaker.spoken == ["wave", "wave"]


def timer_trigger(
    *,
    restart: TimerRestartBehavior = TimerRestartBehavior.START_NEW_TIMER,
    timer_type: TimerType = TimerType.COUNT_DOWN,
    seconds: int = 8,
    show_in_overlay: bool = False,
    ending: TriggerTimerEnding | None = None,
    ended: TriggerTimerEnded | None = None,
    end_early: list[EndEarlyEntry] | None = None,
    timer_name: str = "Word Of Resto",
) -> Trigger:
    return Trigger(
        trigger_enabled=True,
        trigger_name="Resto",
        search_text="body begins to rot",
        use_regex=False,
        timer=TriggerTimer(
            timer_type=timer_type,
            timer_name=timer_name,
            seconds=seconds,
            restart_behavior=restart,
            bar_color="Orange",
            icon_name="Diseased Cloud",
            show_in_overlay=show_in_overlay,
            end_early_texts=end_early or [],
        ),
        timer_ending=ending,
        timer_ended=ended,
    )


def test_timer_starts_with_bar_color_icon_and_overlay_bar() -> None:
    trigger = timer_trigger(show_in_overlay=True)
    _, bus, _, timers, _, published = make_engine(trigger)
    push(bus, "Your body begins to rot.")

    assert timers.added == [("Word Of Resto", 8, "Orange", "Diseased Cloud", "StartNewTimer")]
    bars = [e for e in published if isinstance(e, TimerBarEvent)]
    assert len(bars) == 1
    assert bars[0].name == "Word Of Resto"
    assert bars[0].total_seconds == 8
    assert bars[0].bar_color == "Orange"


def test_timer_name_falls_back_to_trigger_name_and_expands_tokens() -> None:
    trigger = Trigger(
        trigger_enabled=True,
        trigger_name="DT",
        search_text="{npc} begins to cast Death Touch on {target}",
        timer=TriggerTimer(seconds=45, timer_name="--DT-- '{target}'"),
    )
    _, bus, _, timers, _, _ = make_engine(trigger)
    push(bus, "Lord Nagafen begins to cast Death Touch on Gandalf")
    assert timers.added[0][0] == "--DT-- 'Gandalf'"

    unnamed = timer_trigger(timer_name="")
    _, bus2, _, timers2, _, _ = make_engine(unnamed)
    push(bus2, "Your body begins to rot.")
    assert timers2.added[0][0] == "Resto"


def test_restart_behavior_do_nothing_ignores_rematch() -> None:
    trigger = timer_trigger(restart=TimerRestartBehavior.DO_NOTHING)
    _, bus, _, timers, _, _ = make_engine(trigger)
    push(bus, "Your body begins to rot.")
    push(bus, "Your body begins to rot.")
    assert len(timers.added) == 1


def test_restart_behavior_restart_timer_rewinds_countdown() -> None:
    ended = TriggerTimerEnded(
        enabled=True,
        output=TriggerOutput(audio_type=TriggerAudioType.TEXT_TO_SPEECH, tts_text="Resto Now"),
    )
    trigger = timer_trigger(restart=TimerRestartBehavior.RESTART_TIMER, ended=ended)
    engine, bus, speaker, timers, clock, _ = make_engine(trigger)
    push(bus, "Your body begins to rot.")
    clock.advance(6)
    push(bus, "Your body begins to rot.")  # rewinds to 8s remaining
    assert len(timers.added) == 2

    engine.tick(clock.advance(6))  # 12s after first start, 6s after restart
    assert speaker.spoken == []
    engine.tick(clock.advance(2.1))
    assert speaker.spoken == ["Resto Now"]


def test_restart_behavior_start_new_timer_adds_again() -> None:
    trigger = timer_trigger(restart=TimerRestartBehavior.START_NEW_TIMER)
    _, bus, _, timers, _, _ = make_engine(trigger)
    push(bus, "Your body begins to rot.")
    push(bus, "Your body begins to rot.")
    assert len(timers.added) == 2


def test_timer_ending_warning_fires_once_at_threshold() -> None:
    ending = TriggerTimerEnding(
        enabled=True,
        seconds=3,
        output=TriggerOutput(audio_type=TriggerAudioType.TEXT_TO_SPEECH, tts_text="3 seconds"),
    )
    trigger = timer_trigger(seconds=8, ending=ending)
    engine, bus, speaker, _, clock, _ = make_engine(trigger)
    push(bus, "Your body begins to rot.")

    engine.tick(clock.advance(4))  # 4s remaining, above threshold
    assert speaker.spoken == []
    engine.tick(clock.advance(2))  # 2s remaining, inside threshold
    assert speaker.spoken == ["3 seconds"]
    engine.tick(clock.advance(0.5))  # still inside; must not repeat
    assert speaker.spoken == ["3 seconds"]


def test_timer_ended_fires_and_timer_is_removed() -> None:
    ended = TriggerTimerEnded(
        enabled=True,
        output=TriggerOutput(
            display_text_enabled=True,
            display_text="Resto Now",
            display_text_color="Gold",
            audio_type=TriggerAudioType.TEXT_TO_SPEECH,
            tts_text="Resto Now",
        ),
    )
    trigger = timer_trigger(seconds=8, ended=ended)
    engine, bus, speaker, _, clock, published = make_engine(trigger)
    push(bus, "Your body begins to rot.")

    engine.tick(clock.advance(8.1))
    assert speaker.spoken == ["Resto Now"]
    overlay = [e for e in published if isinstance(e, OverlayEvent) and not e.reset]
    assert overlay[-1].text == "Resto Now"
    assert overlay[-1].foreground == "Gold"

    engine.tick(clock.advance(10))  # timer gone; nothing fires again
    assert speaker.spoken == ["Resto Now"]


def test_repeating_timer_rearms_on_expiry() -> None:
    trigger = timer_trigger(timer_type=TimerType.REPEATING_TIMER, seconds=8)
    engine, bus, _, timers, clock, _ = make_engine(trigger)
    push(bus, "Your body begins to rot.")
    assert len(timers.added) == 1
    engine.tick(clock.advance(8.1))
    assert len(timers.added) == 2  # re-armed
    engine.tick(clock.advance(8.1))
    assert len(timers.added) == 3


def test_end_early_text_cancels_active_timer() -> None:
    trigger = timer_trigger(
        seconds=400,
        end_early=[EndEarlyEntry(search_text=r"^\.exp", use_regex=True)],
        ended=TriggerTimerEnded(
            enabled=True,
            output=TriggerOutput(audio_type=TriggerAudioType.TEXT_TO_SPEECH, tts_text="Pop"),
        ),
    )
    engine, bus, speaker, timers, clock, _ = make_engine(trigger)
    push(bus, "Your body begins to rot.")
    push(bus, ".exp")
    assert timers.cancelled == ["Word Of Resto"]
    engine.tick(clock.advance(500))
    assert speaker.spoken == []  # cancelled timers never fire Ended


def test_counter_increments_and_resets_after_inactivity_window() -> None:
    trigger = Trigger(
        trigger_enabled=True,
        trigger_name="Exp",
        search_text="^You gain experience",
        basic=TriggerOutput(
            display_text_enabled=True,
            display_text="Exp [{counter}]",
        ),
        counter=TriggerCounter(reset_enabled=True, minutes=30),
    )
    engine, bus, _, _, clock, published = make_engine(trigger)
    push(bus, "You gain experience!!")
    push(bus, "You gain experience!!")

    overlay = [e for e in published if isinstance(e, OverlayEvent) and not e.reset]
    assert [e.text for e in overlay] == ["Exp [1]", "Exp [2]"]

    # under the 30-minute window: counter keeps going
    engine.tick(clock.advance(29 * 60))
    push(bus, "You gain experience!!")
    overlay = [e for e in published if isinstance(e, OverlayEvent) and not e.reset]
    assert overlay[-1].text == "Exp [3]"

    # a full quiet window resets the tally to zero
    engine.tick(clock.advance(31 * 60))
    push(bus, "You gain experience!!")
    overlay = [e for e in published if isinstance(e, OverlayEvent) and not e.reset]
    assert overlay[-1].text == "Exp [1]"


def test_sound_file_output_uses_injected_player() -> None:
    played: list[str] = []
    bus = EventBus()
    engine = TriggerEngine(
        bus=bus,
        player=ActivePlayer(name="Gandalf"),
        speaker=FakeSpeaker(),
        timers=FakeTimers(),
        clock=Clock(),
        sound_player=played.append,
    )
    engine.set_triggers(
        [
            Trigger(
                trigger_enabled=True,
                search_text="ding",
                use_regex=False,
                basic=TriggerOutput(audio_type=TriggerAudioType.SOUND_FILE, sound_file="ding.wav"),
            )
        ]
    )
    push(bus, "ding")
    assert played == ["ding.wav"]


def test_close_unsubscribes() -> None:
    trigger = Trigger(
        trigger_enabled=True,
        search_text="hello",
        use_regex=False,
        basic=TriggerOutput(audio_type=TriggerAudioType.TEXT_TO_SPEECH, tts_text="hi"),
    )
    engine, bus, speaker, _, _, _ = make_engine(trigger)
    engine.close()
    push(bus, "hello")
    assert speaker.spoken == []
