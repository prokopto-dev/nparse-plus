"""GINA trigger import — read .gtp packages (and raw GINA XML) into Triggers.

A GINA "trigger package" (.gtp) is a zip archive holding one XML document of
nested ``<TriggerGroup>`` elements; the same shape appears inside
GINAConfig.xml, so bare ``.xml`` exports parse too. EQTool's trigger model
descends from GINA's, so almost every field maps 1:1 onto :class:`Trigger`
and no token translation is needed: GINA's ``{S}``/``{S1}``… placeholders are
exactly our ``{word}`` capture semantics, ``{C}`` is the character name in
both tools, and .NET ``(?<name>…)`` groups are rewritten by the model's own
pattern compiler.

Deliberately skipped GINA features (no equivalent here): sound media files,
copy-to-clipboard, phrase modifiers, and overlay assignments. A trigger that
fails to map is counted, not fatal — one bad entry must not sink a pack.
"""

from __future__ import annotations

import io
import zipfile
from xml.etree import ElementTree

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

_TIMER_TYPES = {
    "timer": TimerType.COUNT_DOWN,
    "countdown": TimerType.COUNT_DOWN,
    "stopwatch": TimerType.STOPWATCH,
    "repeatingtimer": TimerType.REPEATING_TIMER,
    # GINA's "Counter" type has no timer bar; the occurrence counting maps to
    # our TriggerCounter block (UseCounterResetTimer below).
    "counter": TimerType.NO_TIMER,
    "notimer": TimerType.NO_TIMER,
}

_RESTART_BEHAVIORS = {
    "startnewtimer": TimerRestartBehavior.START_NEW_TIMER,
    "restartcurrenttimer": TimerRestartBehavior.RESTART_TIMER,
    "restarttimer": TimerRestartBehavior.RESTART_TIMER,
    "ignoreifrunning": TimerRestartBehavior.DO_NOTHING,
    "donothing": TimerRestartBehavior.DO_NOTHING,
}


def _text(elem: ElementTree.Element, tag: str, default: str = "") -> str:
    child = elem.find(tag)
    if child is None or child.text is None:
        return default
    return child.text


def _bool(elem: ElementTree.Element, tag: str) -> bool:
    return _text(elem, tag).strip().lower() == "true"


def _int(elem: ElementTree.Element, tag: str, default: int = 0) -> int:
    try:
        return int(_text(elem, tag).strip() or default)
    except ValueError:
        return default


def _output_from(elem: ElementTree.Element | None) -> TriggerOutput:
    if elem is None:
        return TriggerOutput()
    use_tts = _bool(elem, "UseTextToVoice")
    return TriggerOutput(
        display_text_enabled=_bool(elem, "UseText"),
        display_text=_text(elem, "DisplayText"),
        audio_type=TriggerAudioType.TEXT_TO_SPEECH if use_tts else TriggerAudioType.NONE,
        tts_text=_text(elem, "TextToVoiceText"),
        interrupt_speech=_bool(elem, "InterruptSpeech"),
    )


def _map_trigger(elem: ElementTree.Element, path: list[str]) -> Trigger:
    timer_type = _TIMER_TYPES.get(
        _text(elem, "TimerType").replace(" ", "").lower(), TimerType.NO_TIMER
    )
    millis = _int(elem, "TimerMillisecondDuration")
    if millis > 0:
        seconds, milliseconds = divmod(millis, 1000)
    else:
        seconds, milliseconds = _int(elem, "TimerDuration"), 0
    timer = TriggerTimer(
        timer_type=timer_type,
        timer_name=_text(elem, "TimerName"),
        seconds=seconds,
        milliseconds=milliseconds,
        restart_behavior=_RESTART_BEHAVIORS.get(
            _text(elem, "TimerStartBehavior").replace(" ", "").lower(),
            TimerRestartBehavior.START_NEW_TIMER,
        ),
        # GINA timers always show in its timer window.
        show_in_overlay=timer_type != TimerType.NO_TIMER,
        end_early_texts=[
            EndEarlyEntry(
                search_text=_text(ender, "EarlyEndText"),
                use_regex=_bool(ender, "EnableRegex"),
            )
            for ender in elem.findall("TimerEarlyEnders/EarlyEnder")
            if _text(ender, "EarlyEndText")
        ],
    )
    comments = _text(elem, "Comments")
    if len(path) > 1:
        folder_note = "GINA folder: " + " / ".join(path)
        comments = f"{comments}\n{folder_note}" if comments else folder_note
    search_text = _text(elem, "TriggerText")
    # GINA's plain-text mode still wildcard-matches {S} placeholders; our
    # non-regex mode is a pure substring test, so tokenized patterns must run
    # as regex (where {word} placeholders become capture groups).
    use_regex = _bool(elem, "EnableRegex") or "{" in search_text
    return Trigger(
        trigger_enabled=True,
        trigger_name=_text(elem, "Name"),
        category=path[0] if path else "Imported",
        comments=comments,
        search_text=search_text,
        use_regex=use_regex,
        basic=_output_from(elem),
        timer=timer,
        timer_ending=TriggerTimerEnding(
            enabled=_bool(elem, "UseTimerEnding"),
            seconds=_int(elem, "TimerEndingTime"),
            output=_output_from(elem.find("TimerEndingTrigger")),
        ),
        timer_ended=TriggerTimerEnded(
            enabled=_bool(elem, "UseTimerEnded"),
            output=_output_from(elem.find("TimerEndedTrigger")),
        ),
        counter=TriggerCounter(
            reset_enabled=_bool(elem, "UseCounterResetTimer"),
            seconds=_int(elem, "CounterResetDuration"),
        ),
    )


def _walk(elem: ElementTree.Element, path: list[str], out: list, skipped: list[int]) -> None:
    for child in elem:
        if child.tag == "TriggerGroup":
            _walk(child, [*path, _text(child, "Name", "Imported")], out, skipped)
        elif child.tag == "Trigger":
            try:
                out.append(_map_trigger(child, path))
            except Exception:
                skipped[0] += 1
        elif child.tag not in ("Name", "Comments"):
            # Container elements (TriggerGroups, Triggers, share wrappers).
            _walk(child, path, out, skipped)


def load_gina_triggers(data: bytes) -> tuple[list[Trigger], int]:
    """Parse a .gtp package or raw GINA XML.

    Returns ``(triggers, skipped_count)``; raises ``ValueError`` when the
    payload isn't GINA data or holds no triggers at all.
    """
    if data[:4] == b"PK\x03\x04":
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                names = archive.namelist()
                xml_name = next((n for n in names if n.lower().endswith(".xml")), None)
                if xml_name is None:
                    raise ValueError("no XML document inside the package")
                data = archive.read(xml_name)
        except zipfile.BadZipFile as exc:
            raise ValueError(f"unreadable package: {exc}") from exc
    try:
        root = ElementTree.fromstring(data)
    except ElementTree.ParseError as exc:
        raise ValueError(f"not GINA trigger XML: {exc}") from exc

    triggers: list[Trigger] = []
    skipped = [0]
    _walk(root, [], triggers, skipped)
    if not triggers and not skipped[0]:
        raise ValueError("no triggers found in the file")
    return triggers, skipped[0]
