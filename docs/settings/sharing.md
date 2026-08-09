# Settings → Sharing

The network switch, and the optional pigparse.org account. Background:
[Sharing & PigParse](../features/sharing.md).

![Sharing settings](../assets/screenshots/settings--sharing.png)

| Setting | What it does |
|---|---|
| **Location sharing** | The global mode: **pigparse** (the public hub EQTool uses), **nparse** (self-hostable websocket), or **off**. Applies after restart. |

Per-character everyone/guild-only/off switches and the Share timers toggle
live on the [Character](character.md) page — the global mode picks the
network; the character settings decide what that character sends.

## pigparse.org account

| Control | What it does |
|---|---|
| **Log in with Discord…** | Opens your browser for pigparse.org's Discord OAuth login; the status line shows progress. The resulting token is stored locally in your settings. |
| **Log out** | Clears the token. |

An account is **not** required for map dots, shared timers, or feeds — only
for uploading to pigparse.org's character browser (below), and not even for
that if you send your inventory to p99planner instead.

## Inventory upload

One picker, **Send inventory dumps to**, because both destinations publish
the same character to a different website:

| Destination | What it needs | What happens |
|---|---|---|
| **Off** (default) | — | Dumps stay on this machine, in the [Character Dumps](../windows/character-dumps.md) library. |
| **pigparse.org character page** | The Discord login above | Typing `/outputfile inventory` in game uploads the dump to your pigparse.org character page. |
| **p99planner.com** | Nothing | The export is staged at p99planner.com and a review page opens in your browser, where you approve the import. No account, no API key, no login. |

p99planner never applies anything without you approving it on that page, and
later dumps in the same session join the *same* review link for 24 hours. The
link is private — treat it like a password; see
[Character Dumps](../windows/character-dumps.md#uploading-your-inventory).

Whichever you pick, uploading is fed by the same scan that fills the
Character Dumps library, so that window's **auto-import** has to be on (it is
by default) for a dump to be noticed automatically. Only dumps you take
during the current session upload on their own; ones already sitting in the
EQ folder at launch are collected locally but not published. To send those —
or a whole mule roster at once — use **Upload inventory** in the Character
Dumps window.

Changing the destination applies after a restart.
