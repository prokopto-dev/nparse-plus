# Event overlay regions

*(SDK 1.5+)*

An add-on can claim a **region** inside the Event Overlay — the translucent,
always-on-top surface that draws the CH lanes, the alert headline and the
timer bars over EverQuest — and put its own text, images or status panel
there instead of in a window of its own.

**Read this first: a region is a paint surface, and it will never receive a
click.** Not a mouse press, not a hover, not a wheel, not a key, not a
tooltip, not a context menu — outside position mode and inside it. That is
permanent, by design, and it is not a gap waiting to be filled. **If your
add-on needs input, it wants a window** — `ctx.add_window` and
[`PluginWindow`](developing.md#windows) — which is a real top-level window and
behaves like one. A region *shows* you where you are; it does not pan, zoom,
scroll, expand or answer a button.

The reason is one Qt flag. The overlay sets
`Qt.WindowType.WindowTransparentForInput` on the whole window so your clicks
land in the game behind it, and Qt has no per-child exemption from a
top-level flag. Carving input out for one region would mean a *second*
always-on-top window stacked on the overlay — which is `PluginWindowSpec`
with extra steps. So the honest routing is the one above.

!!! warning "Position mode is the trap"
    Position mode (tray → **Position Event Overlay**) *drops* that flag, so
    the overlay can be dragged. A raw widget in a region would therefore
    start receiving real clicks there and nowhere else — a bug that only
    appears while the user is rearranging their chrome. `PluginOverlayRegion`
    seals itself and everything under it against that, and you should
    subclass it rather than hand back a bare `QWidget`. The seal also keeps
    the *overlay's* own dragging working: the press has to fall through to
    the overlay, which hit-tests the region rectangles itself.

## What this does not deliver

A **minimap** is the example everyone reaches for, and this alone is not
enough to build one. A map needs your live player location and Qt-free zone
geometry exposed to plugins, which is
[#156](https://github.com/prokopto-dev/nparse-plus/issues/156) and is not
built. What works today with nothing else: **text, images and status
panels** — a kill ticker, a countdown you compute yourself, a raid-role
reminder, a picture.

## Declaring one

```python
from nparseplus_sdk import NParsePlugin, OverlayRegionSpec, PluginMeta


class TickerPlugin(NParsePlugin):
    meta = PluginMeta(id="kill-ticker", name="Kill Ticker", requires_sdk=">=1.5,<2")

    def __init__(self):
        self.region = None

    def activate(self, ctx):
        ctx.add_overlay_region(
            OverlayRegionSpec(
                key="kills",                  # unique within your plugin
                title="Kills",                # the position-mode chip label
                factory=self._make,
                has_content=self._has_content,   # required — see below
                default_anchor="bottom",         # "top" | "center" | "bottom"
                default_dy=-140,                 # px off that anchor line
                default_width=200,
            )
        )

    def _make(self, rctx):
        from nparseplus_sdk.ui import PluginOverlayRegion   # lazy: needs Qt

        self.region = PluginOverlayRegion(rctx)
        return self.region

    def _has_content(self):
        return self.region is not None and self.region.has_content()
```

`ctx.add_overlay_region` mirrors `ctx.add_window` exactly: the spec is data,
the app materializes it on the GUI thread, and it is torn down again when the
plugin is disabled. Enabling and disabling take effect **immediately**, with
no restart — including while position mode is up.

The region's key is namespaced `plugin.<id>.<key>`, the same convention window
keys follow. Declare the same `key` twice and only the first region is kept
(the second would share the first's placement); the app logs a warning.

### `has_content` is required, and why

The overlay hides itself when there is nothing to show, so it does not sit as
an empty always-on-top rectangle over the game. It decides by asking **every**
region whether it currently has anything — so a region with no opinion could
never keep the overlay on screen by itself.

It is asked on the GUI thread on every visibility pass, which is every overlay
event. **Keep it a flag read, not a computation.** If it raises, the app logs
once and treats the region as empty for the rest of the session.

## The base widget

`nparseplus_sdk.ui.PluginOverlayRegion` is the region counterpart of
`PluginWindow`. Subclassing it gives you:

- the **non-interactive posture** described above, applied to your whole
  widget tree as it grows;
- the app's **overlay type treatment and active skin**, re-applied live on
  every skin, font-size and frame-opacity change;
- a zero-margin `QVBoxLayout` to fill (`self.layout()`);
- **sample content** so the region is visible and draggable in position mode
  before it has any real content;
- a transparent background — the overlay window is translucent, so any opaque
  brush you paint is a solid rectangle over the game.

```python
from nparseplus_sdk import skin
from nparseplus_sdk.ui import PluginOverlayRegion
from PySide6.QtWidgets import QLabel


class KillTicker(PluginOverlayRegion):
    def __init__(self, rctx):
        super().__init__(rctx)
        self._rows = []
        # A region is display-only, so the Qt bridge (or a QTimer) is how
        # anything in here ever changes.
        rctx.bridge.event_received.connect(self._on_event)

    def add(self, victim):
        row = QLabel(victim, self)
        row.setObjectName(skin.ROW_NAME)      # wears the skin with no rules
        self.layout().insertWidget(0, row)
        self._rows.insert(0, row)
        self.notify_content_changed()         # <- tell the overlay

    def has_content(self):
        return bool(self._rows)

    def sample(self):
        made = [QLabel(name, self) for name in ("Sample Mob", "Another Mob")]
        for label in made:
            label.setObjectName(skin.ROW_NAME)
            self.layout().addWidget(label)
            label.show()
        return made

    def skin_stylesheet(self):
        app = skin.current()
        return f"#{skin.ROW_NAME} {{ {app.typography(skin.BODY_TEXT, color=app.text)} }}"
```

### `notify_content_changed()` — call it liberally

The overlay cannot see inside your region. It anchors each one from its size
and asks `has_content` only when something else prompts a visibility pass. So
when you add a row, remove one, or go from empty to occupied, **say so** —
otherwise your region sits at its old height, or the overlay stays hidden
while you are holding something the user wanted to see.

One call covers all three consequences: the region is re-anchored, the overlay
re-decides whether it is worth showing, and anything you built since is
re-sealed against input.

If you hand back a plain `QWidget` instead of subclassing the base, call
`rctx.on_content_changed()` directly — it is the same hook.

### `sample()` — position mode

While the user is placing their chrome, the overlay fills every region with
sample content so an empty one is still something they can see and drag. Add
your widgets to `self.layout()` and return them; the overlay takes them out
again when position mode ends. The default is a single chip carrying your
region's title, which is enough to make any region placeable — override it to
show the shape of your real content.

### `apply_skin()` / `skin_stylesheet()`

Identical in contract to [`PluginWindow`'s](appearance.md): override
`skin_stylesheet()` for QSS, override `apply_skin()` (calling
`super().apply_skin()` first) for what a stylesheet cannot express. Neither is
called during `super().__init__()` — the first dress is deferred until your
constructor has finished, so anything you assign after `super().__init__(...)`
is available to them. Read `skin.current()` when you paint, never at
`activate`: a skin change is live and mid-fight.

Unlike `PluginWindow`, this class owns the widget's **whole** stylesheet and
does not adopt one you set with `setStyleSheet` — nothing predates SDK 1.5, so
there is no legacy sheet to preserve. Put your rules in `skin_stylesheet()`.

## Placement, and what persists

`default_anchor` / `default_dx` / `default_dy` / `default_width` /
`default_height` are only where the region **starts**. The user drags and
resizes it from tray → **Position Event Overlay** exactly like a built-in
region, and their placement is persisted under the region key in
`window_layouts`.

That placement is deliberately **kept when the plugin is disabled or
uninstalled**, so re-enabling or reinstalling brings the region back where the
user put it rather than to its default. Nothing prunes it — a stale
`"plugin.<id>.<key>"` entry in `settings.json` is a few bytes and costs
nothing, and losing someone's carefully-placed chrome to a reinstall costs
more.

## Threading and cost

- The factory, `has_content`, `sample`, `apply_skin` and everything you build
  run on the **GUI thread**.
- Nothing supervises a region's `paintEvent` or its own `QTimer` the way the
  driver supervises a plugin tick. A region repainting at 30 fps under
  `WindowStaysOnTopHint` is a frame-rate tax on the game, and the only way out
  is Settings → Plugins — which sits *underneath* the overlay that is
  stuttering. Repaint when something changed, not on a clock.
- **`isVisible()` is not a work gate here.** A child of a hidden window is
  never visible, and the overlay hides itself whenever every region is empty —
  so the usual "only poll while shown" idiom reads as "never". Gate on your
  own state instead.
- One `QGraphicsEffect` per widget: the alert headline already spends its slot
  on a drop shadow, and the same limit applies to anything you add.

## The shipped example

`examples/plugins/kill_ticker.py` is the reference add-on for this page — a
list of recent kills that appears when something dies and clears on a zone.
Validate it with `nparseplus-plugin validate kill_ticker.py`.
