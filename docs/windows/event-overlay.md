# Event Overlay

The Event Overlay is a full-screen, fully click-through transparent layer
that sits over the game and renders these kinds of content:

- **Alert text** — big centered outlined messages from
  [triggers](../features/triggers.md) (raid AOE warnings, failed feign,
  charm break, …), in the trigger's color, cleared automatically.
- **Timer bars** — countdown bars stacked bottom-center (AOE countdowns,
  trigger timers). Re-triggering a bar restarts it; bars vanish at zero.
- **CH chain lanes** — one lane per heal target with green chips sliding
  across it, one per CH call. See [CH chains](../features/ch-chains.md). When
  the **CH cadence indicator** is on, a muted marker on a lane's second-cell
  shows the cadence the raid leader called ([Settings → Audio & Overlays](../settings/audio-overlays.md)).
- **Utility** — a dedicated header section for utility alerts (rebuff
  requests, out-of-mana). The **Rebuff Request** and **Out of Mana** built-in
  [triggers](../features/triggers.md) (in the Utility folder, disabled by
  default) render here.

![Event Overlay showing an alert and timer bar](../assets/screenshots/window--event-overlay.png)

The **Utility** section groups rebuff/out-of-mana alerts under their own header
(a CH-lane cadence marker rides above it):

![The Event Overlay's Utility section with rebuff and out-of-mana lines](../assets/screenshots/feature--overlay-utility.png)

It never intercepts clicks, has no window chrome at all, and hides itself
when there's nothing to show — most of the time you forget it exists until
a dragon roars.

## Positioning it

Tray → **Position Event Overlay** shows the overlay's outline with a size
grip so you can drag and resize it to sit exactly over your game window.
**Double-click to lock it in place** when you're done. The geometry
persists. In position mode each region (CH chains, Alerts, Timer bars, and
the Utility section) can be dragged independently to lay out the overlay.

!!! tip
    Make the overlay match your EQ window, not your whole monitor — alert
    text centers within the overlay region, so a full-monitor overlay on a
    windowed game puts alerts outside the game view.

## Tuning

In [Settings → Audio & Overlays](../settings/audio-overlays.md):

- **Alert-text duration** — how long alert text stays up (default 4 s)
  unless the trigger clears it earlier.
- **CH-lane retention** — how long an idle CH lane lingers after its last
  call (default 20 s), so healers keep a stable anchor per heal target.
