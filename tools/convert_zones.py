#!/usr/bin/env python
"""Convert EQTool's EQToolShared/Zones.cs into src/nparseplus/data/zones.json.

One-shot (but rerunnable) converter. It is a real parser -- a small state
machine over the C# text -- not a hand transcription. It extracts:

- ZoneInfoMap entries (per-zone respawn times, notable NPCs, per-NPC spawn
  times, AOE-casting NPCs, map-level settings)
- BoatInfo entries (boat schedules/announcements)
- KaelFactionMobs list
- ZoneNameMapper / ZoneWhoMapper alias dictionaries

Usage:
    .venv/bin/python tools/convert_zones.py [path-to-eqtool-checkout]

The EQTool checkout path may also be given via the EQTOOL_SRC env var.

C# TimeSpan expressions are converted to integer seconds:
    new TimeSpan(h, m, s) / new TimeSpan(ticks) /
    TimeSpan.FromMinutes(x) / TimeSpan.FromHours(x) / TimeSpan.FromSeconds(x)

The default zone respawn used by EQTool (ZoneSpawnTimes.GetSpawnTime fallback
in Zones.cs) is new TimeSpan(0, 6, 40) = 400 seconds; recorded in "meta".
"""

import json
import os
import re
import sys
from pathlib import Path

SOURCE_FILE = "EQToolShared/Zones.cs"
SOURCE_COMMIT = "fdd3f25a274defade4e6330c5b7724144a11000b"

REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_PATH = REPO_ROOT / "src" / "nparseplus" / "data" / "zones.json"


# ---------------------------------------------------------------------------
# Low-level C# text helpers
# ---------------------------------------------------------------------------
def strip_line_comments(text: str) -> str:
    """Remove // comments, respecting string literals."""
    out = []
    i, n = 0, len(text)
    in_string = False
    while i < n:
        ch = text[i]
        if in_string:
            if ch == "\\" and i + 1 < n:
                out.append(text[i : i + 2])
                i += 2
                continue
            if ch == '"':
                in_string = False
            out.append(ch)
            i += 1
        elif ch == '"':
            in_string = True
            out.append(ch)
            i += 1
        elif ch == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] != "\n":
                i += 1
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def balanced_block(text: str, open_idx: int) -> tuple[str, int]:
    """Return (inner_text, index_after_close) for the brace block opening at open_idx."""
    assert text[open_idx] == "{", f"expected '{{' at {open_idx}"
    depth = 0
    in_string = False
    i = open_idx
    while i < len(text):
        ch = text[i]
        if in_string:
            if ch == "\\":
                i += 1
            elif ch == '"':
                in_string = False
        elif ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[open_idx + 1 : i], i + 1
        i += 1
    raise ValueError("unbalanced braces")


def split_top_level(text: str, sep: str = ",") -> list[str]:
    """Split on sep at zero brace/paren depth, respecting string literals."""
    parts = []
    depth = 0
    in_string = False
    cur = []
    i = 0
    while i < len(text):
        ch = text[i]
        if in_string:
            cur.append(ch)
            if ch == "\\":
                i += 1
                cur.append(text[i])
            elif ch == '"':
                in_string = False
        elif ch == '"':
            in_string = True
            cur.append(ch)
        elif ch in "{(":
            depth += 1
            cur.append(ch)
        elif ch in "})":
            depth -= 1
            cur.append(ch)
        elif ch == sep and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
        i += 1
    parts.append("".join(cur))
    return [p.strip() for p in parts if p.strip()]


def parse_string_literal(raw: str) -> str:
    raw = raw.strip()
    if raw == "string.Empty":
        return ""
    m = re.fullmatch(r'@?"(.*)"', raw, re.DOTALL)
    if not m:
        raise ValueError(f"not a string literal: {raw!r}")
    body = m.group(1)
    if not raw.startswith("@"):
        body = body.replace('\\"', '"').replace("\\\\", "\\")
    return body


def timespan_to_seconds(raw: str) -> int:
    """Convert a C# TimeSpan expression to integer seconds."""
    raw = raw.strip()
    m = re.fullmatch(r"new\s+TimeSpan\s*\(([^)]*)\)", raw)
    if m:
        args = [int(a.strip()) for a in m.group(1).split(",")]
        if len(args) == 1:  # ticks (100ns units)
            return args[0] // 10_000_000
        if len(args) == 3:  # hours, minutes, seconds
            h, mi, s = args
            return h * 3600 + mi * 60 + s
        if len(args) == 4:  # days, hours, minutes, seconds
            d, h, mi, s = args
            return d * 86400 + h * 3600 + mi * 60 + s
        raise ValueError(f"unsupported TimeSpan arity: {raw!r}")
    m = re.fullmatch(r"TimeSpan\.From(Hours|Minutes|Seconds)\s*\(([\d.]+)\)", raw)
    if m:
        unit, val = m.group(1), float(m.group(2))
        factor = {"Hours": 3600, "Minutes": 60, "Seconds": 1}[unit]
        seconds = val * factor
        return int(seconds)
    raise ValueError(f"unrecognized TimeSpan expression: {raw!r}")


def parse_number(raw: str) -> float | int:
    raw = raw.strip()
    if not re.fullmatch(r"[\d\s+\-*/.()]+", raw):
        raise ValueError(f"not a numeric expression: {raw!r}")
    value = eval(raw)  # arithmetic only -- guarded by the regex above
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def parse_object_props(body: str) -> dict[str, str]:
    """Parse 'Name = value, ...' object-initializer body into {prop: raw_value}."""
    props = {}
    for part in split_top_level(body):
        m = re.match(r"^\s*(\w+)\s*=\s*(.*)$", part, re.DOTALL)
        if not m:
            raise ValueError(f"cannot parse property: {part[:80]!r}")
        props[m.group(1)] = m.group(2).strip()
    return props


def parse_string_list(raw: str) -> list[str]:
    """Parse new List<string>() { "a", "b" } -> ["a", "b"] (drops empty placeholders)."""
    brace = raw.find("{")
    if brace == -1:  # e.g. `new List<string>()` with no initializer
        return []
    body, _ = balanced_block(raw, brace)
    items = [parse_string_literal(p) for p in split_top_level(body)]
    return [i for i in items if i.strip()]


def parse_npc_spawn_times(raw: str) -> list[dict]:
    """Parse new List<NpcSpawnTime> { new NpcSpawnTime { Name=..., RespawnTime=... }, ... }."""
    out = []
    for entry_body in iter_new_object_bodies(raw, "NpcSpawnTime"):
        props = parse_object_props(entry_body)
        out.append(
            {
                "name": parse_string_literal(props["Name"]),
                "seconds": timespan_to_seconds(props["RespawnTime"]),
            }
        )
    return out


def parse_npcs_that_aoe(raw: str) -> list[dict]:
    """Parse new List<NPCThatAOE> { new NPCThatAOE { Name=..., SpellEffects=... }, ... }."""
    out = []
    for entry_body in iter_new_object_bodies(raw, "NPCThatAOE"):
        props = parse_object_props(entry_body)
        out.append(
            {
                "name": parse_string_literal(props["Name"]),
                "spell_effects": parse_string_list(props["SpellEffects"]),
            }
        )
    return out


def iter_new_object_bodies(text: str, class_name: str):
    """Yield initializer bodies of every `new <class_name> { ... }` in text."""
    for m in re.finditer(rf"new\s+{class_name}\s*(?:\(\s*\))?\s*{{", text):
        body, _ = balanced_block(text, m.end() - 1)
        yield body


# ---------------------------------------------------------------------------
# Section extractors
# ---------------------------------------------------------------------------
def extract_zones(text: str) -> dict[str, dict]:
    zones = {}
    for m in re.finditer(r'ZoneInfoMap\.Add\(\s*"([^"]+)"\s*,\s*new\s+ZoneInfo\s*{', text):
        key = m.group(1)
        body, _ = balanced_block(text, m.end() - 1)
        props = parse_object_props(body)
        # Properties omitted in the C# object initializer take the C# defaults:
        # bool false, double 0, TimeSpan.Zero, empty lists.
        zone = {
            "name": parse_string_literal(props["Name"]) if "Name" in props else key,
            "show_all_map_levels": props.get("ShowAllMapLevels", "false").strip() == "true",
            "zone_level_height": parse_number(props.get("ZoneLevelHeight", "0")),
            "respawn_seconds": timespan_to_seconds(props.get("RespawnTime", "new TimeSpan(0)")),
            "notable_npcs": parse_string_list(props.get("NotableNPCs", "")),
            "npc_spawn_times": parse_npc_spawn_times(props.get("NpcSpawnTimes", "")),
            "npc_contains_spawn_times": parse_npc_spawn_times(
                props.get("NpcContainsSpawnTimes", "")
            ),
            "npcs_that_aoe": parse_npcs_that_aoe(props.get("NPCThatAOE", "")),
        }
        known = {
            "Name",
            "ShowAllMapLevels",
            "ZoneLevelHeight",
            "RespawnTime",
            "NotableNPCs",
            "NpcSpawnTimes",
            "NpcContainsSpawnTimes",
            "NPCThatAOE",
        }
        unknown = set(props) - known
        if unknown:
            raise ValueError(f"zone {key!r} has unhandled properties: {sorted(unknown)}")
        zones[key] = zone
    return zones


def extract_boats(text: str) -> list[dict]:
    boats = []
    for m in re.finditer(r"Boats\.Add\(\s*new\s+BoatInfo\s*{", text):
        body, _ = balanced_block(text, m.end() - 1)
        props = parse_object_props(body)
        boat_enum = props["Boat"].strip().split(".")[-1]
        boats.append(
            {
                "boat": boat_enum,
                "pretty_name": parse_string_literal(props["PrettyName"]),
                "start_announcement": parse_string_literal(props["StartAnnoucement"]),
                "announcement_to_dock_in_seconds": parse_number(
                    props["AnnouncementToDockInSeconds"]
                ),
                "start_point": parse_string_literal(props["StartPoint"]),
                "end_point": parse_string_literal(props["EndPoint"]),
                "trip_time_in_seconds": parse_number(props["TripTimeInSeconds"]),
            }
        )
    return boats


def extract_kael_faction_mobs(text: str) -> list[str]:
    m = re.search(r"KaelFactionMobs\s*=\s*new\s+List<string>\s*\(\s*\)\s*{", text)
    if not m:
        raise ValueError("KaelFactionMobs list not found")
    body, _ = balanced_block(text, m.end() - 1)
    return [parse_string_literal(p) for p in split_top_level(body)]


def extract_mapper(text: str, mapper_name: str) -> dict[str, str]:
    pairs = {}
    for m in re.finditer(rf'{mapper_name}\.Add\(\s*"([^"]*)"\s*,\s*"([^"]*)"\s*\)', text):
        pairs[m.group(1)] = m.group(2)
    return pairs


def main() -> None:
    if len(sys.argv) > 1:
        eqtool_src = Path(sys.argv[1])
    elif os.environ.get("EQTOOL_SRC"):
        eqtool_src = Path(os.environ["EQTOOL_SRC"])
    else:
        sys.exit("usage: convert_zones.py <path-to-eqtool-checkout>  (or set EQTOOL_SRC)")

    source_path = eqtool_src / SOURCE_FILE
    text = strip_line_comments(source_path.read_text(encoding="utf-8-sig"))

    zones = extract_zones(text)
    boats = extract_boats(text)
    kael_faction_mobs = extract_kael_faction_mobs(text)
    who_mapper = extract_mapper(text, "ZoneWhoMapper")
    name_mapper = extract_mapper(text, "ZoneNameMapper")

    # The converter must find exactly as many zones as there are Add() calls.
    add_calls = len(re.findall(r"ZoneInfoMap\.Add\(", text))
    assert len(zones) == add_calls, f"parsed {len(zones)} zones but found {add_calls} Add() calls"
    boat_add_calls = len(re.findall(r"Boats\.Add\(", text))
    assert len(boats) == boat_add_calls, "boat count mismatch"
    who_calls = len(re.findall(r"ZoneWhoMapper\.Add\(", text))
    name_calls = len(re.findall(r"ZoneNameMapper\.Add\(", text))
    assert len(who_mapper) == who_calls, "ZoneWhoMapper count mismatch"
    # ZoneNameMapper may have duplicate keys collapsing in a dict; verify none did.
    assert len(name_mapper) == name_calls, "ZoneNameMapper count mismatch (duplicate keys?)"

    data = {
        "source": {"file": SOURCE_FILE, "commit": SOURCE_COMMIT},
        "meta": {
            "default_respawn_seconds": 400,
            "notes": (
                "Spawn-time lookup order (ZoneSpawnTimes.GetSpawnTime in Zones.cs): "
                "exact case-insensitive match in npc_spawn_times, then case-insensitive "
                "substring match in npc_contains_spawn_times, then the zone's "
                "respawn_seconds, then the global default of 400 seconds (6:40). "
                "Zone-name resolution (Zones.TranslateToMapName): lower-case/trim the "
                "input, apply aliases.zone_who_mapper (/who long names), then "
                "aliases.zone_name_mapper ('You have entered' long names -> short "
                "names), then require the result to be a key of zones. Empty-string "
                "placeholder entries in NotableNPCs were dropped."
            ),
        },
        "zones": zones,
        "boats": boats,
        "kael_faction_mobs": kael_faction_mobs,
        "aliases": {
            "zone_who_mapper": who_mapper,
            "zone_name_mapper": name_mapper,
        },
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    # Sanity check: reload and report counts.
    loaded = json.loads(OUTPUT_PATH.read_text(encoding="utf-8"))
    n_spawn = sum(len(z["npc_spawn_times"]) for z in loaded["zones"].values())
    n_contains = sum(len(z["npc_contains_spawn_times"]) for z in loaded["zones"].values())
    n_aoe = sum(len(z["npcs_that_aoe"]) for z in loaded["zones"].values())
    print(f"wrote {OUTPUT_PATH}")
    print(f"zones: {len(loaded['zones'])}")
    print(f"boats: {len(loaded['boats'])}")
    print(f"kael_faction_mobs: {len(loaded['kael_faction_mobs'])}")
    print(f"zone_who_mapper aliases: {len(loaded['aliases']['zone_who_mapper'])}")
    print(f"zone_name_mapper aliases: {len(loaded['aliases']['zone_name_mapper'])}")
    print(f"npc_spawn_times entries: {n_spawn}")
    print(f"npc_contains_spawn_times entries: {n_contains}")
    print(f"npcs_that_aoe entries: {n_aoe}")


if __name__ == "__main__":
    main()
