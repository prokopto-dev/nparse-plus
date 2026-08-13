"""Trigger engine — Python port of EQTool's trigger system."""

from nparseplus.core.triggers.builtin import (
    effective_triggers,
    load_builtin_triggers,
    sync_builtin_triggers,
)
from nparseplus.core.triggers.chat_commands import CustomTimerChatCommands, parse_custom_timer
from nparseplus.core.triggers.engine import Speaker, TimerSink, TriggerEngine
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
from nparseplus.core.triggers.suggest import (
    TriggerSuggestion,
    neutralize_output_tokens,
    strip_timestamp,
    suggest_trigger_name,
    suggest_trigger_text,
)

__all__ = [
    "CustomTimerChatCommands",
    "EndEarlyEntry",
    "Speaker",
    "TimerRestartBehavior",
    "TimerSink",
    "TimerType",
    "Trigger",
    "TriggerAudioType",
    "TriggerCounter",
    "TriggerEngine",
    "TriggerOutput",
    "TriggerSuggestion",
    "TriggerTimer",
    "TriggerTimerEnded",
    "TriggerTimerEnding",
    "effective_triggers",
    "load_builtin_triggers",
    "neutralize_output_tokens",
    "parse_custom_timer",
    "strip_timestamp",
    "suggest_trigger_name",
    "suggest_trigger_text",
    "sync_builtin_triggers",
]
