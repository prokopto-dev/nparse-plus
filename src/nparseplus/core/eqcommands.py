"""EQ slash commands and macro tokens, for macro-line autocomplete (Qt-free).

A curated list of the client commands and ``%`` substitutions a Titanium-era
P99 character can use in a social, with one-line descriptions. Unlike the
zone/spell data this is **hand-maintained, not generated** — there is no
machine-readable command list in the client to convert, and the EQTool source
carries none either. It is deliberately conservative: commands that only exist
on Live are omitted, because suggesting a command P99 will reject is worse
than not suggesting it at all.

Only used to *offer* completions. Nothing validates a macro line against this
list — players use `/` commands from custom UIs and future patches, so an
unrecognised command is perfectly legitimate.
"""

from __future__ import annotations

from dataclasses import dataclass

#: ``%`` substitutions the client expands when a social runs.
TOKENS: list[tuple[str, str]] = [
    ("%T", "Your current target's name"),
    ("%N", "Your own name"),
    ("%S", "Your target's name in the possessive (e.g. \"Fippy's\")"),
    ("%G", "Your target's race/class group descriptor"),
    ("%R", "Your target's race"),
    ("%C", "Your target's class"),
    ("%Z", "The zone you are in"),
    ("%%", "A literal percent sign"),
]


@dataclass(frozen=True)
class EqCommand:
    command: str
    description: str
    #: Broad grouping, shown after the description in the completer popup.
    category: str


def _c(command: str, description: str, category: str) -> EqCommand:
    return EqCommand(command=command, description=description, category=category)


COMMANDS: list[EqCommand] = [
    # -- Combat & targeting
    _c("/assist", "Assist your target, taking their target", "Combat"),
    _c("/attack", "Toggle melee attack", "Combat"),
    _c("/attack on", "Turn melee attack on", "Combat"),
    _c("/attack off", "Turn melee attack off", "Combat"),
    _c("/target", "Target a player or NPC by name", "Combat"),
    _c("/autofire", "Toggle ranged auto-fire", "Combat"),
    _c("/consider", "Consider your target's difficulty", "Combat"),
    _c("/duel", "Challenge your target to a duel", "Combat"),
    _c("/yell", "Yell for help", "Combat"),
    _c("/disarm", "Attempt to disarm your target (rogue/warrior)", "Combat"),
    _c("/taunt", "Taunt your target", "Combat"),
    _c("/hide", "Toggle Hide", "Combat"),
    _c("/sneak", "Toggle Sneak", "Combat"),
    _c("/backstab", "Backstab your target (rogue)", "Combat"),
    _c("/kick", "Kick your target", "Combat"),
    _c("/bash", "Bash your target", "Combat"),
    _c("/slam", "Slam your target (ogre)", "Combat"),
    _c("/fishing", "Cast your fishing pole", "Combat"),
    _c("/doability", "Use the ability in a combat-ability slot (1-5)", "Combat"),
    # -- Chat
    _c("/say", "Speak to everyone nearby", "Chat"),
    _c("/shout", "Shout to the whole zone", "Chat"),
    _c("/ooc", "Speak out of character, zone-wide", "Chat"),
    _c("/auction", "Post to the auction channel", "Chat"),
    _c("/tell", "Send a private message", "Chat"),
    _c("/reply", "Reply to the last tell you received", "Chat"),
    _c("/group", "Speak to your group", "Chat"),
    _c("/guild", "Speak to your guild", "Chat"),
    _c("/raidsay", "Speak to your raid", "Chat"),
    _c("/emote", "Perform a custom emote", "Chat"),
    _c("/rand", "Roll a random number", "Chat"),
    _c("/random", "Roll a random number in a range", "Chat"),
    _c("/who", "List players in your zone", "Chat"),
    _c("/who all", "List players across the server", "Chat"),
    _c("/afk", "Toggle away-from-keyboard", "Chat"),
    _c("/anon", "Toggle anonymous", "Chat"),
    _c("/roleplay", "Toggle roleplay flag", "Chat"),
    _c("/lfg", "Toggle looking-for-group", "Chat"),
    _c("/note", "Write a note to your log file", "Chat"),
    # -- Group & raid
    _c("/invite", "Invite your target to your group", "Group"),
    _c("/disband", "Leave your group, or remove a member", "Group"),
    _c("/follow", "Auto-follow your target", "Group"),
    _c("/makeleader", "Give group leadership to your target", "Group"),
    _c("/raidinvite", "Invite your target to your raid", "Group"),
    _c("/raidaccept", "Accept a raid invitation", "Group"),
    _c("/raiddisband", "Leave the raid, or remove a member", "Group"),
    _c("/split", "Split coin with your group", "Group"),
    _c("/corpse", "Drag a nearby corpse toward you", "Group"),
    _c("/consent", "Let a player drag your corpse", "Group"),
    _c("/deny", "Revoke corpse-dragging consent", "Group"),
    # -- Pets
    _c("/pet attack", "Send your pet at your target", "Pet"),
    _c("/pet back off", "Call your pet off", "Pet"),
    _c("/pet follow me", "Make your pet follow you", "Pet"),
    _c("/pet guard here", "Make your pet guard this spot", "Pet"),
    _c("/pet guard me", "Make your pet guard you", "Pet"),
    _c("/pet sit", "Make your pet sit", "Pet"),
    _c("/pet stand", "Make your pet stand", "Pet"),
    _c("/pet taunt", "Make your pet taunt", "Pet"),
    _c("/pet no taunt", "Stop your pet taunting", "Pet"),
    _c("/pet hold", "Make your pet hold (no auto-aggro)", "Pet"),
    _c("/pet get lost", "Dismiss your pet permanently", "Pet"),
    _c("/pet leader", "Ask who your pet's leader is", "Pet"),
    _c("/pet report health", "Make your pet report its health", "Pet"),
    # -- Character & movement
    _c("/sit", "Sit down", "Character"),
    _c("/stand", "Stand up", "Character"),
    _c("/duck", "Crouch", "Character"),
    _c("/camp", "Camp out of the game", "Character"),
    _c("/camp desktop", "Camp straight to the desktop", "Character"),
    _c("/exit", "Exit the game immediately", "Character"),
    _c("/loc", "Print your current location", "Character"),
    _c("/time", "Show the in-game and local time", "Character"),
    _c("/played", "Show your played time", "Character"),
    _c("/inventory", "List your inventory", "Character"),
    _c("/hotbutton", "Create or assign a hot button", "Character"),
    _c("/useitem", "Use the item in a given slot", "Character"),
    _c("/cast", "Cast the spell in a gem slot (1-8)", "Character"),
    _c("/memorize", "Memorize a spell into a gem slot", "Character"),
    _c("/book", "Open your spell book", "Character"),
    _c("/track", "Open the tracking window", "Character"),
    _c("/bandage", "Bind wounds on your target", "Character"),
    _c("/forage", "Attempt to forage", "Character"),
    _c("/sense heading", "Sense your heading", "Character"),
    _c("/mend", "Attempt to mend (monk)", "Character"),
    _c("/feign", "Feign death (monk)", "Character"),
    _c("/open", "Open your target (chest, door)", "Character"),
    _c("/pick", "Attempt to pick locks (rogue)", "Character"),
    # -- Interface & logging
    _c("/log on", "Start writing a log file", "Interface"),
    _c("/log off", "Stop writing a log file", "Interface"),
    _c("/outputfile inventory", "Dump your inventory to a text file", "Interface"),
    _c("/outputfile guild", "Dump your guild roster to a text file", "Interface"),
    _c("/outputfile raid", "Dump your raid roster to a text file", "Interface"),
    _c("/friend", "Add or remove a friend", "Interface"),
    _c("/ignore", "Add or remove someone from your ignore list", "Interface"),
    _c("/filter", "Open the chat filter window", "Interface"),
    _c("/loadskin", "Reload the UI, optionally a named skin", "Interface"),
    _c("/viewport", "Adjust the 3D viewport", "Interface"),
    _c("/showgrass", "Toggle ground clutter", "Interface"),
    _c("/fps", "Toggle the frame-rate display", "Interface"),
    _c("/help", "List available commands", "Interface"),
    # -- Trade & merchants
    _c("/trade", "Open a trade with your target", "Trade"),
    _c("/buyer", "Toggle buyer mode", "Trade"),
    _c("/barter", "Open the barter window", "Trade"),
    _c("/bazaar", "Open the bazaar search", "Trade"),
    _c("/give", "Give an item to your target", "Trade"),
]

#: Every completion string the editor offers, commands first then tokens.
COMPLETIONS: list[str] = [c.command for c in COMMANDS] + [t for t, _d in TOKENS]

_BY_COMMAND = {c.command: c for c in COMMANDS}
_TOKEN_HELP = dict(TOKENS)


def describe(text: str) -> str:
    """One-line help for a command or token, or ``""`` if we don't know it."""
    stripped = text.strip()
    command = _BY_COMMAND.get(stripped)
    if command is not None:
        return command.description
    return _TOKEN_HELP.get(stripped.upper(), "")


def category(text: str) -> str:
    """The grouping for a known command, or ``""``."""
    command = _BY_COMMAND.get(text.strip())
    return command.category if command is not None else ""


def matching(prefix: str, *, limit: int = 0) -> list[str]:
    """Completions starting with ``prefix`` (case-insensitive), in list order."""
    needle = prefix.strip().lower()
    if not needle:
        return list(COMPLETIONS[:limit]) if limit else list(COMPLETIONS)
    hits = [c for c in COMPLETIONS if c.lower().startswith(needle)]
    return hits[:limit] if limit else hits
