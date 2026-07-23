# Merchant Prices — nParse+ example plugin

Tracks the items you offer for sale (your own `You auction, 'WTS …'` lines)
and shows their PigParse price history (6-month WTS average) in an overlay
window. This is the reference plugin exercising the full v1 SDK: parser,
storage, tick, threaded PigParse fetches, an overlay window, and a settings
page.

## Install

- **In-app:** zip the `merchant_prices` folder itself (so the archive
  contains `merchant_prices/__init__.py`, …) and use
  Settings > Plugins > *Install from file…*, or
- **Manual:** copy the `merchant_prices` folder into your plugins directory
  (tray > *Open Plugins Folder*), then restart nParse+.

On first load nParse+ asks for consent — plugins are ordinary Python running
with the app's permissions.

## Develop

```bash
pip install nparseplus-sdk
nparseplus-plugin validate merchant_prices
```

Prices only resolve while a sharing mode with PigParse reachability is
plausible (the plugin still works with sharing off — the host gives plugins
their own API client), and your character must have a known server (`/who`
yourself once after logging in).
