# Appearance

Everything about how nParse+ *looks*, in one place: the skin its in-fight
overlays wear, the window theme, and how hard an on-game alert pushes.

| Setting | What it does |
|---|---|
| **Overlay skin** | The frame, type hierarchy and bar geometry of every overlay — and of the on-game alert. Applies live; no restart. |
| **Theme** | Dark or Light, for the settings and utility windows. After restart. |
| **UI / overlay font size** | Base size for nParse+ UI and overlay typography, including the small tracked labels shared by every skin. Applies live after **Apply & Save**; no restart. |
| **Alert headline size** | Height of only the large alert word on the game (ENRAGED, FTE…). It is independent of the base font size. |
| **Alert emphasis** | Plain, a slow pulse, or the pulse plus a colored halo. |
| **Alert text shadow** | Soft shadow behind on-game alert text. Turning it off removes a per-repaint blur — try it first if the overlay stutters, especially on macOS. |
| **Frame opacity** | Fades the skin's frame and glass only. Countdowns, bars and icons keep full contrast. |

## Skins

A skin is a bigger palette. Where the theme answers "what color is body
text", a skin answers "what does the window's edge look like, how loud is
the title, is the progress bar a thin rule under the row or the row's own
background". Three ship, and the picker previews each one live — click a
card and the timer window and the on-game alert both follow immediately.

| Skin | Looks like |
|---|---|
| **Duxa** *(default)* | Thin double-line frame over flat black glass with tan caps — what a P99 raider running DuxaUI already has on screen, so nParse+ stops being the odd window out. |
| **Velious plate** | The full classic frame: beveled stone edge, notched corners, gems in recessed sockets, engraved gold caps. Loudest personality, biggest footprint. |
| **Ledger** | The Duxa frame, but the bar *is* the row — a draining block behind the name. Least to scan mid-pull; reads at a glance from the corner of the eye. |

Every skin keeps the row colors the rest of the app already uses —
beneficial green, detrimental red, cooldown blue, custom-timer purple,
roll amber — and the same numbers in the same order. What changes is the
frame, the type hierarchy, and where your eye lands. All three use the
bundled Noto Sans family: compact headings and captions share the same
tracked uppercase treatment, while rows and values stay plain for quick
reading.

!!! tip "Switching without opening Settings"
    The tray menu has a **UI Skin** submenu next to Window Layouts. Skins
    apply live, so you can change one mid-fight.

## Frame opacity vs. window opacity

These are different controls and it matters which one you reach for.

- **Window opacity** ([Windows](windows.md)) fades the *whole window* —
  the frame, the labels and the countdowns with it. Turn it down far
  enough and you cannot read the numbers you turned it down to see past.
- **Frame opacity** (here) fades *only* the skin's plate and glass. The
  text, bars and gem icons stay at full strength.

The map has its own version of the same split — see
[Maps → Transparency](maps.md#transparency).

## Applying appearance changes

The skin picker previews live, so **Close** puts the skin back the way it
was. **Apply & Save** is what makes a preview durable and applies font-size,
alert and frame-opacity changes to open overlays without restarting. The
window theme still takes effect after restart.
