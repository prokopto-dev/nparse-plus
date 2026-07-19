#!/usr/bin/env python
"""Port EQTool's EQTool/Models/BuiltInTriggers.cs to src/nparseplus/data/builtin_triggers.json.

The C# file builds its trigger list in code (helper methods Build/BuildLegacy plus
an AoeSpec table), so this converter embeds a careful transcription of that data
and mirrors the same helper functions in Python, then emits JSON. It verifies
trigger counts and BuiltInId uniqueness before writing.

Structure notes (mirroring the C# Trigger/TriggerComponents models):
- Every trigger has IsBuiltIn=true, Category="Built In", TriggerEnabled=false.
- TriggerId is Guid.NewGuid() at runtime in C# (not stable), so the stable
  identifier here is built_in_id.
- Colors are WPF color names exactly as written in the C# source.
- timer duration fields are kept as minutes/seconds as-written, plus a
  computed duration_seconds.

Usage:
    .venv/bin/python tools/convert_builtin_triggers.py
"""

import json
from pathlib import Path

SOURCE_FILE = "EQTool/Models/BuiltInTriggers.cs"
SOURCE_COMMIT = "fdd3f25a274defade4e6330c5b7724144a11000b"

REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_PATH = REPO_ROOT / "src" / "nparseplus" / "data" / "builtin_triggers.json"

CATEGORY = "Built In"

EXPECTED_NAMED = 16  # explicit Create*() triggers in All()
EXPECTED_LEGACY = 15  # BuildLegacy(...) rows in All()
EXPECTED_AOE = 34  # rows in the EncounterAoes table (lines 415-448)
EXPECTED_NPARSEPLUS = 2  # nparseplus-only utility built-ins (#14; not from EQTool)


def output(
    display_text: str | None = None,
    *,
    display_text_enabled: bool = False,
    display_text_color: str | None = None,
    audio_type: str = "None",
    tts_text: str = "",
    overlay_section: str = "alert",
) -> dict:
    """Mirror of C# TriggerOutput (only fields BuiltInTriggers.cs sets).

    ``overlay_section`` is an nparseplus extension (#14): "alert" (default,
    center text) or "utility" (dedicated utility header section). Only emitted
    when non-default so the EQTool-ported triggers' JSON is unchanged.
    """
    out = {
        "display_text_enabled": display_text_enabled,
        "audio_type": audio_type,
        "tts_text": tts_text,
    }
    if display_text is not None:
        out["display_text"] = display_text
    if display_text_color is not None:
        out["display_text_color"] = display_text_color
    if overlay_section != "alert":
        out["overlay_section"] = overlay_section
    return out


def timer(
    timer_name: str,
    *,
    minutes: int = 0,
    seconds: int = 0,
    restart_behavior: str,
    bar_color: str | None = None,
    icon_name: str | None = None,
    show_in_overlay: bool = False,
    end_early_texts: list | None = None,
) -> dict:
    """Mirror of C# TriggerTimer with TimerType=CountDown."""
    t = {
        "timer_type": "CountDown",
        "timer_name": timer_name,
        "minutes": minutes,
        "seconds": seconds,
        "duration_seconds": minutes * 60 + seconds,
        "restart_behavior": restart_behavior,
        "show_in_overlay": show_in_overlay,
    }
    if bar_color is not None:
        t["bar_color"] = bar_color
    if icon_name is not None:
        t["icon_name"] = icon_name
    if end_early_texts is not None:
        t["end_early_texts"] = end_early_texts
    return t


def base_trigger(
    built_in_id: str,
    name: str,
    search_text: str,
    use_regex: bool,
    folder: str | None,
) -> dict:
    return {
        "is_built_in": True,
        "built_in_id": built_in_id,
        "built_in_folder": folder,
        "trigger_enabled": False,
        "trigger_name": name,
        "search_text": search_text,
        "use_regex": use_regex,
        "category": CATEGORY,
    }


def build(
    built_in_id: str,
    name: str,
    search_text: str,
    use_regex: bool,
    display_text: str,
    tts_text: str,
    folder: str | None = None,
) -> dict:
    """Mirror of the C# Build() helper (TTS-audio basic output, red text)."""
    t = base_trigger(built_in_id, name, search_text, use_regex, folder)
    t["basic"] = output(
        display_text,
        display_text_enabled=True,
        display_text_color="Red",
        audio_type="TextToSpeech",
        tts_text=tts_text,
    )
    return t


def build_legacy(
    built_in_id: str,
    name: str,
    search_text: str,
    display_text: str,
    audio_text: str,
    folder: str | None = None,
) -> dict:
    """Mirror of the C# BuildLegacy() helper: display-only (AudioType None), regex on."""
    t = base_trigger(built_in_id, name, search_text, True, folder)
    t["basic"] = output(
        display_text,
        display_text_enabled=True,
        display_text_color="Red",
        audio_type="None",
        tts_text=audio_text,
    )
    return t


# ---------------------------------------------------------------------------
# The 16 explicitly-constructed triggers (Create* methods), transcribed.
# ---------------------------------------------------------------------------
def named_triggers() -> list[dict]:
    triggers = []

    # CreateEnraged (folder assigned after Build in C#)
    t = build(
        "builtin:enraged",
        "Enraged",
        "{npc} has become ENRAGED.",
        True,
        "{npc} ENRAGED",
        "{npc} is enraged",
    )
    t["built_in_folder"] = "Encounters"
    triggers.append(t)

    triggers.append(
        build(
            "builtin:levitate-fading",
            "Levitate Fading",
            "You feel as if you are about to fall.",
            False,
            "Levitate Fading",
            "Levitate Fading",
            "Utility",
        )
    )
    triggers.append(
        build(
            "builtin:invis-fading",
            "Invis Fading",
            "You feel yourself starting to appear.",
            False,
            "Invisability Fading.",
            "Invisability Fading.",
            "Utility",
        )
    )
    triggers.append(
        build(
            "builtin:failed-feign",
            "Failed Feign",
            "{c} has fallen to the ground.",
            True,
            "{c} Feign Failed Death!",
            "{c} Failed Feign Death",
            "Combat",
        )
    )
    triggers.append(
        build(
            "builtin:group-invite",
            "Group Invite",
            "{name} invites you to join a group.",
            True,
            "{name} Invites you to a group",
            "{name} Invites you to a group",
            "Utility",
        )
    )
    triggers.append(
        build(
            "builtin:npc-gating",
            "NPC Gating",
            "{npc} begins to cast the gate spell.",
            True,
            "{npc} begins to Gate",
            "{npc} begins to Gate",
            "Combat",
        )
    )
    triggers.append(
        build(
            "builtin:charm-break",
            "Charm Break",
            "Your charm spell has worn off.",
            False,
            "Charm Break",
            "Charm Break",
            "Combat",
        )
    )

    # CreateDeathTouch
    t = base_trigger(
        "builtin:death-touch",
        "Death Touch (Fright/Dread)",
        r"^(?<npc>Fright|Dread) says,? '(?<target>[^' ]+)'",
        True,
        "Encounters",
    )
    t["zone"] = "fear"
    t["basic"] = output(
        "Death Touch: {target}",
        display_text_enabled=True,
        display_text_color="White",
        audio_type="TextToSpeech",
        tts_text="Death touch on {target}",
    )
    t["timer"] = timer(
        "--DT-- '{target}'",
        seconds=45,
        restart_behavior="RestartTimer",
        bar_color="Red",
    )
    triggers.append(t)

    # CreateTellsYou
    triggers.append(
        build(
            "builtin:tells-you",
            "Tells You",
            r"^(?<name>[\w`]+)( tells you, '| -> {c}: )"
            r"(?!I'll give you|Attacking |Welcome to my bank|Come back soon)",
            True,
            "{name} sent a tell",
            "{name} sent a tell",
            "Utility",
        )
    )

    # CreateExpGained
    t = base_trigger(
        "builtin:exp-gained-0640",
        "Exp Timer",
        r"^You gain (party )?experience",
        True,
        "Utility",
    )
    t["comments"] = (
        'Note: Sending a tell to ".exp", i.e. "/t .exp" will immediately terminate '
        "all timers of this type!\n"
        "BB fishers = 6:40 and 22:00, Chardok = 18:00, COM = 22:00, "
        "Crystal Caverns = 14:45, Droga = 20:30, Grobb = 24:00, Hole = 21:30, "
        "HS = 20:30, Kael = 28:00, Kedge = 22:00, Lower Guk = 28:00, MM = 23 min, "
        "North Felwithe guards = 24:00, Oasis specs = 16:30, OOT specs/sisters = 6:00, "
        "Paw = 22:00, Perma = 22:00, Seb Lair = 27:00, Skyfire = 13:00, "
        "Skyshrine = 30:00, TD = 12:00, TT = 6:40, Wars Woods brutes = 6:40, "
        "WK guards = 6:00, WL = 14:30, "
    )
    t["basic"] = output(display_text_enabled=False, audio_type="None", tts_text="")
    t["timer"] = timer(
        "-- Exp Timer [{counter}] (.exp)",
        minutes=6,
        seconds=40,
        restart_behavior="StartNewTimer",
        end_early_texts=[{"search_text": r"^\.exp", "use_regex": True}],
    )
    t["timer_ending"] = {
        "enabled": True,
        "seconds": 30,
        "output": output(
            "30 second warning",
            display_text_enabled=True,
            audio_type="TextToSpeech",
            tts_text="30 second warning",
        ),
    }
    t["timer_ended"] = {
        "enabled": True,
        "output": output(
            "Pop [{counter}]",
            display_text_enabled=True,
            audio_type="TextToSpeech",
            tts_text="Pop",
        ),
    }
    t["counter"] = {"reset_enabled": True, "minutes": 30}
    triggers.append(t)

    # CreateResist
    t = base_trigger(
        "builtin:resist",
        "Resist",
        r"^(?:You resist the|Your target resisted the) (?<spell>.+) spell[!.]",
        True,
        "Combat",
    )
    t["basic"] = output(
        "Resisted: {spell}",
        display_text_enabled=True,
        display_text_color="Red",
        audio_type="TextToSpeech",
        tts_text="Resisted",
    )
    triggers.append(t)

    # CreateAvatarOfWarLockout
    t = base_trigger(
        "builtin:aow-lockout",
        "Avatar of War Lockout",
        "The Avatar of War shouts 'Who dares defile my temple?! Come forth and face me!'",
        False,
        "Encounters/Kael",
    )
    t["zone"] = "kael"
    t["timer"] = timer(
        "The Avatar of War Lockout",
        minutes=20,
        restart_behavior="RestartTimer",
        bar_color="Magenta",
        icon_name="Spirit of Wolf",
    )
    triggers.append(t)

    # CreateVPHoskarResto
    t = base_trigger(
        "builtin:vp-hoskar-resto",
        "Hoskar Resto",
        r"(body begins to rot\.|You resist the Diseased Cloud spell!)",
        True,
        "Encounters/VP",
    )
    t["zone"] = "veeshan"
    t["timer"] = timer(
        "Word Of Resto",
        seconds=8,
        restart_behavior="RestartTimer",
        bar_color="Orange",
        icon_name="Diseased Cloud",
        show_in_overlay=True,
    )
    t["timer_ended"] = {
        "enabled": True,
        "output": output(
            "Resto Now",
            display_text_enabled=True,
            display_text_color="Gold",
            audio_type="TextToSpeech",
            tts_text="Resto Now",
        ),
    }
    triggers.append(t)

    # CreateDragonRoar
    t = base_trigger(
        "builtin:dragon-roar",
        "Dragon Roar",
        r"(You flee in terror\.|You resist the Dragon Roar spell!)",
        True,
        "Encounters",
    )
    t["timer"] = timer(
        "Dragon Roar",
        seconds=36,
        restart_behavior="RestartTimer",
        bar_color="Orange",
        icon_name="Dragon Roar",
        show_in_overlay=True,
    )
    triggers.append(t)

    # CreateSpellWornOff
    triggers.append(
        build(
            "builtin:spell-worn-off",
            "Spell Worn Off",
            r"^Your (?<spell>[\w ]+) spell has worn off\.",
            True,
            "{spell} faded",
            "{spell} faded",
            "Combat",
        )
    )

    # CreateEnteredZone
    triggers.append(
        build(
            "builtin:entered-zone",
            "Entered Zone",
            r"^You have entered (?!an Arena|an area)(?<zone>.+)\.",
            True,
            "You zoned into {zone}",
            "You zoned into {zone}",
            "Utility",
        )
    )

    return triggers


# ---------------------------------------------------------------------------
# The 15 BuildLegacy rows in All(), transcribed.
# (id, name, search_text, display_text, audio_text, folder)
# ---------------------------------------------------------------------------
LEGACY_ROWS = [
    (
        "builtin:spell-interrupted",
        "Spell Interrupted",
        "^Your spell is interrupted.",
        "Spell Interrupted",
        "Interrupted",
        "Combat",
    ),
    (
        "builtin:spell-fizzle",
        "Spell Fizzle",
        "^Your spell fizzles!",
        "Spell Fizzles",
        "Fizzle",
        "Combat",
    ),
    (
        "builtin:backstabber",
        "Backstabber",
        "^{backstabber} backstabs {target} for {damage} points of damage.",
        "{backstabber} backstabs {target} for {damage}",
        "Backstabber",
        "Combat",
    ),
    (
        "builtin:corpse-need-consent",
        "Corpse Need Consent",
        "^You do not have consent to summon that corpse",
        "Need Consent",
        "Need Consent",
        "Utility",
    ),
    (
        "builtin:corpse-out-of-range",
        "Corpse Out of Range",
        "^The corpse is too far away to summon",
        "Corpse OOR",
        "Corpse out of range",
        "Utility",
    ),
    (
        "builtin:select-a-target",
        "Select a Target",
        "^(You must first select a target for this spell)|(You must first click on the being you wish to attack)",
        "Select a target",
        "Select a target",
        "Utility",
    ),
    (
        "builtin:insufficient-mana",
        "Insufficient Mana",
        "^Insufficient Mana to cast this spell!",
        "OOM",
        "out of mana",
        "Combat",
    ),
    (
        "builtin:target-out-of-range",
        "Target Out of Range",
        "^Your target is out of range",
        "Target out of range",
        "Out of range",
        "Combat",
    ),
    (
        "builtin:spell-did-not-take-hold",
        "Spell Did Not Take Hold",
        "^Your spell did not take hold",
        "Spell did not take hold",
        "Spell did not take hold",
        "Combat",
    ),
    (
        "builtin:must-be-standing",
        "Must be standing to cast",
        "^(You must be standing)|(You are too distracted to cast a spell now)",
        "Stand up!",
        "stand up",
        "Combat",
    ),
    (
        "builtin:dispelled",
        "Dispelled",
        "^You feel a bit dispelled",
        "You have been dispelled",
        "dispelled",
        "Combat",
    ),
    (
        "builtin:regen-faded",
        "Regen Faded",
        "^You have stopped regenerating",
        "===== Regen faded =====",
        "re-gen faded",
        "Utility",
    ),
    (
        "builtin:cant-see-target",
        "Can't See Target",
        "^You can't see your target",
        "Can't see target",
        "Can't see target",
        "Combat",
    ),
    (
        "builtin:sense-heading",
        "Sense Heading",
        "^You think you are heading {direction}",
        "Direction = {direction}",
        "{direction}",
        "Utility",
    ),
    (
        "builtin:sense-heading-failed",
        "Sense Heading Failed",
        "^You have no idea what direction you are facing",
        "No idea",
        "no idea",
        "Utility",
    ),
]


# ---------------------------------------------------------------------------
# The EncounterAoes table (BuiltInTriggers.cs lines 413-449), transcribed.
# Defaults (from the C# AoeSpec class): id=None (generated), bar_color="Orange",
# minutes=0, seconds=12, show_in_overlay=True, alert_text=False.
# ---------------------------------------------------------------------------
def aoe(
    spell,
    name,
    zone,
    folder,
    search,
    id=None,
    bar_color="Orange",
    minutes=0,
    seconds=12,
    show_in_overlay=True,
    alert_text=False,
):
    return {
        "spell": spell,
        "name": name,
        "zone": zone,
        "folder": folder,
        "search": search,
        "id": id,
        "bar_color": bar_color,
        "minutes": minutes,
        "seconds": seconds,
        "show_in_overlay": show_in_overlay,
        "alert_text": alert_text,
    }


ENCOUNTER_AOES = [
    aoe(
        "Stun Breath",
        "Stun Breath",
        "necropolis",
        "Encounters/DN",
        r"(Your eardrums rupture\.|staggers with intense pain\.|You resist the Stun Breath spell!)",
        id="builtin:dn-stun-breath",
        bar_color="Gold",
    ),
    aoe(
        "Cloud of Silence",
        "Cloud of Silence",
        "growthplane",
        "Encounters/POG",
        r"(You are in a cloud of silence\.|is surrounded by a cloud of silence\.)",
        id="builtin:pog-cloud-of-silence",
        minutes=3,
        seconds=0,
        show_in_overlay=False,
        alert_text=True,
    ),
    aoe(
        "Rain of Molten Lava",
        "Rain of Molten Lava",
        "templeveeshan",
        "Encounters/TOV",
        r"(Lava sears your skin\.|skin is seared by lava\.|You resist the Rain of Molten Lava spell!)",
        id="builtin:tov-rain-of-molten-lava",
    ),
    aoe(
        "Wave of Heat",
        "Wave of Heat",
        "templeveeshan",
        "Encounters/TOV",
        r"(A blast of heat sears your skin\.|skin sears\.|You resist the Wave of Heat spell!)",
        id="builtin:tov-wave-of-heat",
    ),
    aoe(
        "Wave of Cold",
        "Wave of Cold",
        "templeveeshan",
        "Encounters/TOV",
        r"(A blast of cold freezes your skin\.|skin freezes\.|You resist the Wave of Cold spell!)",
        id="builtin:tov-wave-of-cold",
        bar_color="DeepSkyBlue",
    ),
    aoe(
        "Frost Breath",
        "Frost Breath - TOV",
        "templeveeshan",
        "Encounters/TOV",
        r"(body freezes as the frost hits you\.|body freezes as the frost hits them\.|You resist the Frost Breath spell!)",
        id="builtin:tov-frost-breath",
        bar_color="DeepSkyBlue",
    ),
    aoe(
        "Frost Breath",
        "Frost Breath - Perma",
        "permafrost",
        "Encounters/VOX",
        r"(body freezes as the frost hits you\.|body freezes as the frost hits them\.|You resist the Frost Breath spell!)",
        id="builtin:vox-frost-breath",
        bar_color="DeepSkyBlue",
    ),
    aoe(
        "Silver Breath",
        "Silver Breath - TOV",
        "templeveeshan",
        "Encounters/TOV",
        r"(You feel your skin freeze\.|skin freezes\.|You resist the Silver Breath spell!)",
        id="builtin:tov-silver-breath",
        bar_color="Orange",
    ),
    aoe(
        "Silver Breath",
        "Silver Breath - WW",
        "westwastes",
        "Encounters/WW",
        r"(You feel your skin freeze\.|skin freezes\.|You resist the Silver Breath spell!)",
        id="builtin:ww-silver-breath",
        bar_color="Orange",
    ),
    aoe(
        "Freezing Breath",
        "Freezing Breath",
        "dreadlands",
        "Encounters/Dreadlands",
        r"(An icy cold shoots through your body|is slowed by the freezing blast\.|You resist the Freezing Breath spell!)",
    ),
    aoe(
        "Ceticious Cloud",
        "Ceticious Cloud",
        "emeraldjungle",
        "Encounters/Emerald Jungle",
        r"(Your legs lock in pain as you choke on the noxious poison\.|doubles over in pain as the noxious poison|You resist the Ceticious Cloud spell!)",
    ),
    aoe(
        "Silver Breath",
        "Silver Breath - POG",
        "growthplane",
        "Encounters/POG",
        r"(You feel your skin freeze\.|skin freezes\.|You resist the Silver Breath spell!)",
    ),
    aoe(
        "Blinding Fear",
        "Blinding Fear",
        "sebilis",
        "Encounters/Sebilis",
        r"(You freeze in terror\.|You resist the Blinding Fear spell!)",
    ),
    aoe(
        "Poison Breath",
        "Poison Breath",
        "sebilis",
        "Encounters/Sebilis",
        r"(A green mist seeps in to your skin\.|You resist the Poison Breath spell!)",
    ),
    aoe(
        "Immolating Breath",
        "Immolating Breath",
        "skyfire",
        "Encounters/Skyfire",
        r"(Your flesh is seared from your bones\.|flesh is seared\.|You resist the Immolating Breath spell!)",
    ),
    aoe(
        "Mind Cloud",
        "Mind Cloud",
        "skyshrine",
        "Encounters/Skyshrine",
        r"(A cloud of pain covers you\.|is covered by a cloud of pain\.|You resist the Mind Cloud spell!)",
        minutes=2,
        seconds=0,
    ),
    aoe(
        "Ice Breath",
        "Ice Breath",
        "skyshrine",
        "Encounters/Skyshrine",
        r"(Shards of magical ice rend you\.|cut by shards of magical ice\.|You resist the Ice Breath spell!)",
        minutes=2,
        seconds=0,
    ),
    aoe(
        "Lava Breath",
        "Lava Breath - Sol B",
        "soldungb",
        "Encounters/Sol B",
        r"(Your body combusts as the lava hits you\.|body combusts as the lava hits them\.|You resist the Lava Breath spell!)",
    ),
    aoe(
        "Lava Breath",
        "Lava Breath - TOV",
        "templeveeshan",
        "Encounters/TOV",
        r"(Your body combusts as the lava hits you\.|body combusts as the lava hits them\.|You resist the Lava Breath spell!)",
    ),
    aoe(
        "Cloud of Disempowerment",
        "Cloud of Disempowerment",
        "templeveeshan",
        "Encounters/TOV",
        r"(You feel your skin freeze\.|skin freezes\.|You resist the Cloud of Disempowerment spell!)",
    ),
    aoe(
        "Electric Blast",
        "Electric Blast",
        "templeveeshan",
        "Encounters/TOV",
        r"(Your body is electrified as lightning strikes you\.|body is electrified as the lightning strikes\.|You resist the Electric Blast spell!)",
    ),
    aoe(
        "Cloud of Fear",
        "Cloud of Fear",
        "templeveeshan",
        "Encounters/TOV",
        r"(Your mind is wracked by fear\.|looks very afraid\.|You resist the Cloud of Fear spell!)",
    ),
    aoe(
        "Bellowing Winds",
        "Bellowing Winds",
        "templeveeshan",
        "Encounters/TOV",
        r"(You begin to spin\.|begins to spin\.|You resist the Bellowing Winds spell!)",
    ),
    aoe(
        "Tsunami",
        "Tsunami",
        "templeveeshan",
        "Encounters/TOV",
        r"(A tsunami crushes you\.|is crushed by a wall of water\.|You resist the Tsunami spell!)",
    ),
    aoe(
        "Wave of Flame",
        "Wave of Flame",
        "templeveeshan",
        "Encounters/TOV",
        r"(You feel your skin burn\.|skin burns\.|You resist the Wave of Flame spell!)",
    ),
    aoe(
        "Scream of Chaos",
        "Scream of Chaos",
        "templeveeshan",
        "Encounters/TOV",
        r"(You experience chaotic weightlessness\.|rises chaotically into the air\.|You resist the Scream of Chaos spell!)",
    ),
    aoe(
        "Chaos Breath",
        "Chaos Breath - TOV",
        "templeveeshan",
        "Encounters/TOV",
        r"(You experience chaotic weightlessness\.|rises chaotically into the air\.|You resist the Chaos Breath spell!)",
    ),
    aoe(
        "Ancient Breath",
        "Ancient Breath",
        "templeveeshan",
        "Encounters/TOV",
        r"(Your life force drains away\.|You resist the Ancient Breath spell!)",
    ),
    aoe(
        "Diseased Cloud",
        "Diseased Cloud - TOV",
        "templeveeshan",
        "Encounters/TOV",
        r"(body begins to rot\.|You resist the Diseased Cloud spell!)",
    ),
    aoe(
        "Mesmerizing Breath",
        "Mesmerizing Breath",
        "veeshan",
        "Encounters/VP",
        r"(You are in a cloud of silence\.|is surrounded by a cloud of silence\.|You resist the Mesmerizing Breath spell!)",
    ),
    aoe(
        "Chaos Breath",
        "Chaos Breath - VP",
        "veeshan",
        "Encounters/VP",
        r"(You experience chaotic weightlessness\.|rises chaotically into the air\.|You resist the Chaos Breath spell!)",
    ),
    aoe(
        "Stun Breath",
        "Stun Breath - VP",
        "veeshan",
        "Encounters/VP",
        r"(Your eardrums rupture\.|staggers with intense pain\.|You resist the Stun Breath spell!)",
    ),
    aoe(
        "Stream of Acid",
        "Stream of Acid",
        "veeshan",
        "Encounters/VP",
        r"(Your body burns as the acid hits you\.|body burns as the acid hits them\.|You resist the Stream of Acid spell!)",
    ),
    aoe(
        "Lightning Breath",
        "Lightning Breath",
        "veeshan",
        "Encounters/VP",
        r"(Your body is electrified as the lightning strikes you\.|body is electrified as the lightning strikes\.|You resist the Lightning Breath spell!)",
    ),
]


def build_aoe_trigger(spec: dict) -> dict:
    """Mirror of the C# BuildAoeTrigger() helper."""
    built_in_id = (
        spec["id"]
        or ("builtin:aoe:" + spec["zone"] + ":" + spec["spell"]).replace(" ", "-").lower()
    )
    t = base_trigger(built_in_id, spec["name"], spec["search"], True, spec["folder"])
    t["zone"] = spec["zone"]
    t["timer"] = timer(
        spec["name"],
        minutes=spec["minutes"],
        seconds=spec["seconds"],
        restart_behavior="RestartTimer",
        bar_color=spec["bar_color"],
        icon_name=spec["spell"],
        show_in_overlay=spec["show_in_overlay"],
    )
    if spec["alert_text"]:
        t["basic"] = output(
            spec["name"],
            display_text_enabled=True,
            display_text_color="Red",
            audio_type="TextToSpeech",
            tts_text=spec["name"],
        )
    return t


def nparseplus_triggers() -> list[dict]:
    """nparseplus-only utility built-ins (#14) — NOT ported from EQTool.

    These route their display text to the dedicated "utility" overlay header
    section (overlay_section="utility"), unlike EQTool's center-text alerts.
    They ship disabled like every other built-in; the "Utility" folder keeps
    them beside the ported utility one-liners.
    """
    triggers = []

    # Rebuff Request: an incoming tell asking for buffs. Same tell-format regex
    # convention as the "Tells You" built-in (a .NET (?<name>) named group).
    t = base_trigger(
        "builtin:np-rebuff-request",
        "Rebuff Request",
        r"^(?<name>[\w` ]+) tells you, '[^']*\b(?:re)?buffs?\b",
        True,
        "Utility",
    )
    t["basic"] = output(
        "Rebuff: {name}",
        display_text_enabled=True,
        display_text_color="Gold",
        audio_type="TextToSpeech",
        tts_text="Buff request from {name}",
        overlay_section="utility",
    )
    triggers.append(t)

    # Out of Mana indicator, routed to the utility section (the ported
    # builtin:insufficient-mana stays a center-text Combat alert).
    t = base_trigger(
        "builtin:np-out-of-mana",
        "Out of Mana",
        "^Insufficient Mana to cast this spell!",
        True,
        "Utility",
    )
    t["basic"] = output(
        "OOM",
        display_text_enabled=True,
        display_text_color="Gold",
        audio_type="None",
        tts_text="out of mana",
        overlay_section="utility",
    )
    triggers.append(t)

    return triggers


def main() -> None:
    named = named_triggers()
    legacy = [build_legacy(*row) for row in LEGACY_ROWS]
    aoes = [build_aoe_trigger(s) for s in ENCOUNTER_AOES]
    nparseplus = nparseplus_triggers()
    triggers = named + legacy + aoes + nparseplus

    assert len(named) == EXPECTED_NAMED, f"named: {len(named)} != {EXPECTED_NAMED}"
    assert len(legacy) == EXPECTED_LEGACY, f"legacy: {len(legacy)} != {EXPECTED_LEGACY}"
    assert len(aoes) == EXPECTED_AOE, f"aoe: {len(aoes)} != {EXPECTED_AOE}"
    assert len(nparseplus) == EXPECTED_NPARSEPLUS, (
        f"nparseplus: {len(nparseplus)} != {EXPECTED_NPARSEPLUS}"
    )
    ids = [t["built_in_id"] for t in triggers]
    assert len(ids) == len(set(ids)), "duplicate built_in_id"
    assert all(i.startswith("builtin:") for i in ids), "bad built_in_id prefix"

    data = {
        "source": {"file": SOURCE_FILE, "commit": SOURCE_COMMIT},
        "meta": {
            "category": CATEGORY,
            "notes": (
                "Transcribed from BuiltInTriggers.cs (C# builds these in code, so "
                "this file is generated from a table embedded in the converter "
                "script). built_in_id is the stable identifier; the C# TriggerId is "
                "a Guid.NewGuid() minted at runtime and is intentionally omitted. "
                "All triggers are seeded disabled (trigger_enabled=false); EQTool "
                "auto-enables builtin:vp-hoskar-resto, builtin:dragon-roar, "
                "builtin:spell-worn-off and builtin:entered-zone on startup. Colors "
                "are WPF color names as-written. Encounter AOE triggers restrict to "
                "their zone short name and fire a CountDown timer; search patterns "
                "are .NET regexes (note (?<name>...) named groups). The Utility "
                "folder also carries nparseplus-only built-ins (#14) that route "
                "their text to the overlay's utility section (overlay_section)."
            ),
            "counts": {
                "named": len(named),
                "legacy": len(legacy),
                "encounter_aoes": len(aoes),
                "nparseplus": len(nparseplus),
                "total": len(triggers),
            },
        },
        "triggers": triggers,
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    loaded = json.loads(OUTPUT_PATH.read_text(encoding="utf-8"))
    print(f"wrote {OUTPUT_PATH}")
    print(f"triggers: {len(loaded['triggers'])} (counts: {loaded['meta']['counts']})")


if __name__ == "__main__":
    main()
