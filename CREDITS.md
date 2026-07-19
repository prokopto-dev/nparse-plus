# Credits

## nParse (upstream)

nParse+ is a fork of [nParse](https://github.com/nomns/nparse) by nomns
("Nomns' Parser for Project 1999"), GPL-3.0.

splash.png:
- Splash screen made by Mindflux of P99Green.
- Original art by Keith A. Parkinson.

## EQTool / PigParse

Feature designs, log-parsing patterns, built-in trigger definitions, zone/respawn/NPC
databases, pet data, and test fixtures are ported from
[EQTool](https://github.com/smasherprog/eqtool) by smasherprog and contributors (MIT).

- Pinned EQTool commits (two, by scope):
  - **Data assets** (zones/respawn/boat, built-in triggers, spell counters) were
    converted from commit `fdd3f25a274defade4e6330c5b7724144a11000b` — the
    `SOURCE_COMMIT` recorded in each `tools/convert_*.py` and stamped into the
    generated `data/*.json`.
  - **Behavior and the network client** (parsers/handlers, the PigParse
    SignalR/REST port) track the later commit
    `d8e8084fe50a4f40b7c632e26be3e48dbced96f5`, the general C# source-of-truth
    reference used in `CLAUDE.md` and `docs/dev-notes/pigparse-api.md`.
- Ported data assets include: `EQToolShared/Zones.cs` (zone/respawn/boat data),
  `EQTool/Models/BuiltInTriggers.cs` (built-in trigger library),
  `EQTool/Files/All_Pet_Names.txt` / `Pet_Spells.txt`,
  `EQToolShared/MasterNPCList.txt` / `MasterItemList.txt` / `items_vendor_prices.csv`,
  `EQTool/Files/VisionFix.zip`, and the `EQtoolsTests` log-line corpus.

## Map data

EQ map files originate from the community Brewall / P99 mapping projects
(see upstream nParse for provenance).
