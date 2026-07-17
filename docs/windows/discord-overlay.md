# Discord Overlay

The Discord overlay (inherited from original nParse) embeds Discord's
StreamKit voice widget as an in-game overlay, so you can see who's in your
voice channel — and who's talking — without alt-tabbing.

![The Discord overlay](../assets/screenshots/window--discord.png)

Open it from the tray → **Discord**; configure it via tray →
**Configure Discord**.

## Setup

1. In Discord, enable the StreamKit overlay for your server (the
   [Discord StreamKit](https://streamkit.discord.com/overlay) page walks
   through authorizing it).
2. Tray → **Configure Discord** — set the overlay URL and appearance
   (text color, background opacity).
3. Toggle the overlay from the tray and drag it into place like any other
   window.

## Notes

- The overlay uses an embedded web view (Qt WebEngine). On some hardened
  Linux kernels the web view's sandbox can't start; the
  [tarball guide](../getting-started/install-linux-tarball.md#wayland-and-environment-defaults)
  explains the `QTWEBENGINE_DISABLE_SANDBOX` default that keeps it working.
  If Qt WebEngine is unavailable entirely, the rest of nParse+ works fine
  without it.
- This is a **legacy** window: it predates the nParse+ rebuild and will be
  modernized eventually, but it works today and EQTool has no equivalent.
