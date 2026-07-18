"""Tests for the GINA .gtp / XML importer (core/triggers/gina.py)."""

import io
import zipfile

import pytest

from nparseplus.core.triggers import gina
from nparseplus.core.triggers.gina import load_gina_triggers
from nparseplus.core.triggers.model import TimerRestartBehavior, TimerType, TriggerAudioType

GINA_XML = b"""<?xml version="1.0" encoding="utf-8"?>
<SharedData>
  <TriggerGroups>
    <TriggerGroup>
      <Name>Raid Pack</Name>
      <Triggers>
        <Trigger>
          <Name>Rune Counter</Name>
          <TriggerText>Your rune flickers</TriggerText>
          <EnableRegex>False</EnableRegex>
          <UseText>True</UseText>
          <DisplayText>Rune hit {COUNTER}</DisplayText>
          <TimerType>Counter</TimerType>
          <UseCounterResetTimer>True</UseCounterResetTimer>
          <CounterResetDuration>30</CounterResetDuration>
        </Trigger>
      </Triggers>
      <TriggerGroups>
        <TriggerGroup>
          <Name>Sebilis</Name>
          <Triggers>
            <Trigger>
              <Name>Caster Alert</Name>
              <TriggerText>{S} begins to cast a spell</TriggerText>
              <Comments>watch adds</Comments>
              <EnableRegex>False</EnableRegex>
              <UseText>True</UseText>
              <DisplayText>{C} sees {S} casting</DisplayText>
              <UseTextToVoice>True</UseTextToVoice>
              <TextToVoiceText>caster incoming</TextToVoiceText>
              <InterruptSpeech>True</InterruptSpeech>
              <TimerType>NoTimer</TimerType>
            </Trigger>
            <Trigger>
              <Name>Mez Timer</Name>
              <TriggerText>You begin casting Mesmerize</TriggerText>
              <EnableRegex>False</EnableRegex>
              <UseText>False</UseText>
              <TimerType>Timer</TimerType>
              <TimerName>Mez</TimerName>
              <TimerDuration>24</TimerDuration>
              <TimerMillisecondDuration>24500</TimerMillisecondDuration>
              <TimerStartBehavior>RestartCurrentTimer</TimerStartBehavior>
              <UseTimerEnding>True</UseTimerEnding>
              <TimerEndingTime>6</TimerEndingTime>
              <TimerEndingTrigger>
                <UseText>True</UseText>
                <DisplayText>Mez ending!</DisplayText>
                <UseTextToVoice>False</UseTextToVoice>
              </TimerEndingTrigger>
              <UseTimerEnded>True</UseTimerEnded>
              <TimerEndedTrigger>
                <UseText>True</UseText>
                <DisplayText>Mez dropped</DisplayText>
              </TimerEndedTrigger>
              <TimerEarlyEnders>
                <EarlyEnder>
                  <EarlyEndText>has been slain</EarlyEndText>
                  <EnableRegex>False</EnableRegex>
                </EarlyEnder>
              </TimerEarlyEnders>
            </Trigger>
          </Triggers>
        </TriggerGroup>
      </TriggerGroups>
    </TriggerGroup>
  </TriggerGroups>
</SharedData>
"""


def _by_name(triggers, name):
    return next(t for t in triggers if t.trigger_name == name)


def _as_gtp(xml: bytes) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("ShareData.xml", xml)
    return buffer.getvalue()


def test_xml_walk_finds_all_triggers_with_categories() -> None:
    triggers, skipped = load_gina_triggers(GINA_XML)
    assert skipped == 0
    assert {t.trigger_name for t in triggers} == {"Rune Counter", "Caster Alert", "Mez Timer"}
    # The full nested folder path joins into one flat category name.
    assert _by_name(triggers, "Rune Counter").category == "Raid Pack"
    caster = _by_name(triggers, "Caster Alert")
    assert caster.category == "Raid Pack / Sebilis"
    assert _by_name(triggers, "Mez Timer").category == "Raid Pack / Sebilis"
    assert all(t.trigger_enabled for t in triggers)
    # Comments carry the trigger's own notes, no folder breadcrumb.
    assert "watch adds" in caster.comments


def test_basic_output_and_tts_map() -> None:
    triggers, _ = load_gina_triggers(GINA_XML)
    caster = _by_name(triggers, "Caster Alert")
    assert caster.basic.display_text_enabled is True
    assert caster.basic.display_text == "{C} sees {S} casting"
    assert caster.basic.audio_type == TriggerAudioType.TEXT_TO_SPEECH
    assert caster.basic.tts_text == "caster incoming"
    assert caster.basic.interrupt_speech is True
    # Plain-text mode with {S} tokens is promoted to regex mode.
    assert caster.use_regex is True
    assert caster.timer.timer_type == TimerType.NO_TIMER


def test_timer_fields_map() -> None:
    triggers, _ = load_gina_triggers(GINA_XML)
    mez = _by_name(triggers, "Mez Timer")
    assert mez.use_regex is False
    timer = mez.timer
    assert timer.timer_type == TimerType.COUNT_DOWN
    assert timer.timer_name == "Mez"
    # Millisecond duration wins over the redundant whole-second field.
    assert timer.duration == pytest.approx(24.5)
    assert timer.restart_behavior == TimerRestartBehavior.RESTART_TIMER
    assert timer.show_in_overlay is True
    assert [(e.search_text, e.use_regex) for e in timer.end_early_texts] == [
        ("has been slain", False)
    ]
    assert mez.timer_ending.enabled is True
    assert mez.timer_ending.threshold == pytest.approx(6.0)
    assert mez.timer_ending.output.display_text == "Mez ending!"
    assert mez.timer_ended.enabled is True
    assert mez.timer_ended.output.display_text == "Mez dropped"


def test_counter_type_maps_to_counter_block() -> None:
    triggers, _ = load_gina_triggers(GINA_XML)
    rune = _by_name(triggers, "Rune Counter")
    assert rune.timer.timer_type == TimerType.NO_TIMER
    assert rune.counter.reset_enabled is True
    assert rune.counter.reset_after == pytest.approx(30.0)


def test_gtp_zip_package_parses_like_the_xml() -> None:
    from_zip, skipped = load_gina_triggers(_as_gtp(GINA_XML))
    assert skipped == 0
    assert {t.trigger_name for t in from_zip} == {"Rune Counter", "Caster Alert", "Mez Timer"}


def test_imported_tokens_fire_through_the_matching_machinery() -> None:
    triggers, _ = load_gina_triggers(GINA_XML)
    caster = _by_name(triggers, "Caster Alert")
    caster.player_name = "Gandalf"
    assert caster.matches("A froglok ilis knight begins to cast a spell.")
    assert caster.expand(caster.basic.display_text) == "Gandalf sees A froglok ilis knight casting"


def test_not_gina_data_raises() -> None:
    with pytest.raises(ValueError, match="not GINA trigger XML"):
        load_gina_triggers(b'{"format": "nparseplus-triggers"}')


def test_xml_without_triggers_raises() -> None:
    with pytest.raises(ValueError, match="no triggers"):
        load_gina_triggers(b"<SharedData><TriggerGroups /></SharedData>")


def test_zip_without_xml_raises() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("readme.txt", "hi")
    with pytest.raises(ValueError, match="no XML"):
        load_gina_triggers(buffer.getvalue())


def test_bad_trigger_is_counted_not_fatal(monkeypatch) -> None:
    real = gina._map_trigger

    def flaky(elem, path):
        trigger = real(elem, path)
        if trigger.trigger_name == "Mez Timer":
            raise RuntimeError("boom")
        return trigger

    monkeypatch.setattr(gina, "_map_trigger", flaky)
    triggers, skipped = load_gina_triggers(GINA_XML)
    assert skipped == 1
    assert {t.trigger_name for t in triggers} == {"Rune Counter", "Caster Alert"}
