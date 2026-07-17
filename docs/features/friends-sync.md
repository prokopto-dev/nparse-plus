# Friends sync

The EQ client keeps a **separate friends list per character** — your main
knows everyone, your alt knows nobody. nParse+ merges them.

## How it works

Each character's list lives in a `[Friends]` section of
`<Name>_<Server>.ini` in the EQ install directory (exactly 100 slots).
nParse+ reads and writes those client files directly:

1. Set your **EQ install directory** in
   [Settings → General](../settings/general.md).
2. In [Settings → Friends](../settings/friends.md), pick the server
   (P1999Green, P1999Blue, P1999Red, or Real-Test).
3. **Load** merges every character's friends list on that server into one
   view; edit it if you like.
4. **Push** writes the merged list back to *all* of that server's
   character ini files.

Log in on any character afterward and `/friends` shows the merged list.

!!! note "Backups first"
    Before the first write, every ini file is copied into a
    `friends_backup/` folder beside it — an nParse+ safety addition over
    EQTool's version, which writes with no backup. If anything looks
    wrong, the originals are right there.

The game only reads the ini files at login, so push while the characters
are logged out (or camp and come back) to see the merged list in game.
