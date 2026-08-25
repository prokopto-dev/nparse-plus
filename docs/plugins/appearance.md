# Making your add-on look like nParse+

*SDK 1.4+.*

nParse+ has a look: three skins (**Duxa**, **Velious plate**, **Ledger**), one
palette, one type scale, all driven by the user's own settings — and the user
can change any of it **live**, from the tray, mid-fight. `nparseplus_sdk.skin`
is how your plugin follows that instead of guessing at it.

Two things you might reach for instead, and why neither works:

- **Hardcoded hex** is stale the moment the user switches skin, and there is
  no restart to hide behind — the switch happens under your open window.
- **`import nparseplus.ui.skins`** is host internals: no compat promise, and
  it is the part of the app most likely to move in a redesign.

The façade is a small, frozen read surface over the same values the app's own
overlays use. It is Qt-free, so you can build a stylesheet in a unit test with
no `QApplication`.

## The one rule

> **The palette owns VALUE. The skin owns HUE.**

Body text, field backgrounds and page grounds come from the **value** group —
`text`, `heading`, `hint`, `disabled`, `surface`, `surface_alt`, `field_bg`,
`field_text`, `field_border`, `panel_bg`, `track`. Those are the readability
floor, they are **identical under all three skins**, and they are what you
paint text and grounds with.

The skin contributes exactly one hue, `accent`, and it belongs at accent
positions: hairlines, selection bands, focus rings, group titles, a button
edge.

So this is right:

```python
app = skin.current()
label.setStyleSheet(f"color: {app.text}; background: {app.surface};")
rule.setStyleSheet(f"background: {app.accent};")          # a hairline
```

and this is the mistake the split exists to prevent:

```python
label.setStyleSheet(f"color: {app.text}; background: {app.accent};")   # ✗
```

An accent is a mark, not a ground. Body text on it measures **1.2:1** under
Velious (gold on gold — the app's own title caps are gold too), **1.7:1** under
Duxa and **3.3:1** under Ledger. All three are unreadable, so this is not a
"looks fine on the skin you developed against" trap — it is simply wrong
everywhere.

When you do want a filled row — a selection, a highlighted entry — `band` is
the ground the app itself uses, and its text comes from the value group like
any other ground:

```python
f"QTableWidget::item:selected {{ background: {app.gradient(app.band)};"
f" color: {app.heading}; }}"
```

Not the skin's caps colour on it, tempting as that is: the app's *config*
chrome pairs those for its sidebar, but on Ledger's band that measures 3.4:1,
and on an `rgba(accent, .28)` tint of your own, 2.9:1. `heading` and `text`
clear AA on every skin's band, which is the whole reason the value group is
skin-independent.

## The snapshot is live — read it when you paint

`skin.current()` returns a frozen `AppSkin`. Read it **at the moment you
paint**, never at `activate()`:

```python
from nparseplus_sdk import skin
from nparseplus_sdk.ui import PluginWindow


class MyWindow(PluginWindow):
    def skin_stylesheet(self) -> str:
        app = skin.current()
        return f"#Total {{ {app.typography(skin.NUMERIC_TEXT, color=app.heading)} }}"
```

`skin_stylesheet()` is **the** place for a plugin window's own QSS. The base
class owns the whole sheet and re-assembles it from its dressing plus your
rules on **every** skin, font-size and frame-opacity change — so it is called
afresh each time, must not cache an `AppSkin`, and neither discards your rules
nor accumulates a stale copy of them per change. It is also called from
`__init__`, before your own widgets exist: return rules, do not touch widgets.

For the work a stylesheet cannot do — styling child widgets, painted colours,
sizes — override `apply_skin()` and call `super().apply_skin()` first:

```python
    def apply_skin(self) -> None:
        super().apply_skin()
        self._row.apply_skin()
```

!!! note "Windows written before SDK 1.4"
    A `PluginWindow` that sets its own sheet with `setStyleSheet` and knows
    nothing of either hook keeps working: the base class adopts that sheet and
    re-applies it *after* its own rules, so your styling still wins and a skin
    change no longer discards it. Moving those rules into `skin_stylesheet()`
    is worth doing, but nothing breaks if you don't.

A window that overrides nothing is still skinned — since SDK 1.4 the base
class paints the active skin's plate and glass behind your window and applies
the overlay stylesheet, so an add-on that writes no styling at all still looks
like the rest of the app under all three skins.

!!! note "Where the call comes from"
    The app sweeps every plugin window on a skin change, duck-typed on the
    method name (`app._apply_appearance`). Your override needs no registration
    — defining `apply_skin()` is the whole subscription.

## Sizes are multipliers, never px

Every size in nParse+ is a multiple of the user's `general.font_size`, so a
plugin that writes `font-size: 11px` stops matching the moment they scale the
UI up. Use the snapshot:

```python
app.px(0.9)                                   # a size, resolved
app.typography(skin.BODY_TEXT)                # family + size + weight
app.typography(skin.SMALL_DISPLAY, color=app.accent)   # tracked caps
app.tracking(0.8, 0.2)                        # letter-spacing in px
```

The three shared type roles are `SMALL_DISPLAY` (tracked uppercase — titles,
section headers), `BODY_TEXT` (ordinary overlay label text) and `NUMERIC_TEXT`
(a countdown, a total — what the eye lands on first). Build your own with
`skin.TypographyRole(scale, weight, tracking_em)` if you need one.

Note that QSS has no `text-transform`: a caps role sets the *type*, and the
uppercasing is yours to do on the string.

## Free styling from three object names

The ready-made overlay stylesheet already styles three names. Stamp one on a
widget and it wears the skin's treatment for that role with no rules of yours:

| Constant | Value | What it dresses |
| --- | --- | --- |
| `skin.TITLE` | `SkinTitle` | a window's own caps title |
| `skin.ROW_NAME` | `SkinRowName` | the left half of an overlay row |
| `skin.ROW_VALUE` | `SkinRowValue` | its countdown or number |

```python
title = QLabel("Merchant Prices", self)
title.setObjectName(skin.TITLE)     # done — caps, tracking, the skin's colour
```

Qt stylesheets cascade down the widget tree, so this works for any descendant
of a widget carrying `overlay_stylesheet()` — which a `PluginWindow` does by
default. A widget you parent somewhere else needs the sheet set on it (or on
one of its ancestors) too.

## Semantic accents

Named for what they **mean**, so your "this is a debuff" is the same red the
Timers window draws: `GOOD`, `BAD`, `COOLDOWN`, `TIMER`, `ROLL`, `POP_WINDOW`,
`LINK`. Several share a hex today; use the one you mean, not the one that
happens to match, so a change to one does not silently repaint the other.

## A worked example: a themed row

A name on the left, a countdown on the right, a bar under it — the shape every
nParse+ overlay row has, styled entirely from the façade:

```python
from PySide6.QtWidgets import QHBoxLayout, QLabel, QProgressBar, QVBoxLayout, QWidget
from nparseplus_sdk import skin


class TimerRowWidget(QWidget):
    def __init__(self, name: str, parent=None) -> None:
        super().__init__(parent)
        self.label = QLabel(name, self)
        self.label.setObjectName(skin.ROW_NAME)     # skinned for free
        self.value = QLabel("00:00", self)
        self.value.setObjectName(skin.ROW_VALUE)
        self.bar = QProgressBar(self)
        self.bar.setTextVisible(False)

        top = QHBoxLayout()
        top.addWidget(self.label, 1)
        top.addWidget(self.value, 0)
        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.bar)
        self.apply_skin()

    def apply_skin(self) -> None:
        app = skin.current()
        self.bar.setFixedHeight(app.px(0.35, minimum=3))
        self.bar.setStyleSheet(app.bar_stylesheet(skin.GOOD))   # or BAD, COOLDOWN…
```

Then call `row.apply_skin()` for every row from your window's own
`apply_skin()`, so a skin change reaches them too.

## Bars

There are two kinds, and they are not interchangeable:

- `app.bar_stylesheet(color)` is the **thin rule under a Timers-window row** —
  a track, a border, and the skin's lit-to-shaded gradient. Pair it with
  `app.px(...)` for the height.
- `app.overlay_bar_stylesheet(color)` is the **wide bar drawn directly on the
  game**, the one the event overlay uses: its own plate, a left-to-right fade,
  and Ledger's left rule instead of a border. Pair it with
  `app.overlay_bar_height`, which is already in px (bar geometry there is
  fixed, not a font multiple).

Pick by where your widget sits. A window that floats over Norrath with its own
frame wants the first; a widget hosted *inside* the event overlay wants the
second, or its bars will not match the ones beside them.

`app.bar_fill(color)` is the fill alone, if you are painting rather than
styling.

## The frame

If you draw your own plate rather than letting `PluginWindow` do it:

- `plate` / `plate_border` are the outer edge, `glass` / `glass_border` the
  inner surface content sits on. Each fill is a tuple: one stop is a flat
  colour, two are a vertical gradient — `app.gradient(app.plate)` renders
  either.
- `frame_opacity` (0..1) fades **the plate and glass only**. Text, bars and
  icons stay at full contrast — that split is the entire reason it exists
  separately from window opacity, so never apply it to content.
- `notch` is the Velious corner cut in px (`0` on the other two), `plate_padding`
  the gap between plate and glass, and `frame_inset()` the clearance content
  needs to stay off the frame.
- `chip_fill` / `chip_text` are for a small opaque plate sitting **on the
  game** rather than on black glass, and `lane_bg` / `lane_border` for a lane
  on the event overlay.

## Config windows

A settings page or an editor is not an overlay: it is read at a desk, not
glanced at over a raid. `app.config_stylesheet()` is what Settings, the
trigger editor and the plugin manager wear. Set it **on your window**, never
on the `QApplication` — it carries bare type selectors (`QLineEdit`,
`QPushButton`, …) and at app scope those would land on the overlays sitting on
top of EverQuest.

Settings pages you contribute through `ctx.add_settings_page` need none of
this: they are built inside the app's own window and inherit its chrome.

## `AppSkin` reference

`skin.current()` returns this. Grouped by owner, because the grouping *is* the
contract — the value group is safe to pair with itself, the hue group is safe
only as an accent on it.

| Value (the palette's — identical under every skin) | |
| --- | --- |
| `text` | body text |
| `heading` | emphasised text |
| `hint` | a de-emphasised caption |
| `disabled` | a control out of reach right now |
| `surface` / `surface_alt` | a config ground and a raised strip on it |
| `field_bg` / `field_text` / `field_border` | an input field |
| `panel_bg` | the translucent fill an overlay's content sits on |
| `track` | a progress track |

| Hue (the skin's — changes with the user's pick) | |
| --- | --- |
| `accent` | hairlines, focus rings, group titles — a mark, never a ground |
| `band` | the fill behind a selected row; pair with `heading` / `text` |
| `hairline` | the rule between sections |
| `plate` / `plate_border` | the outer frame |
| `glass` / `glass_border` | the inner surface |
| `chip_fill` / `chip_text` | a small opaque plate on the game |
| `lane_bg` / `lane_border` | an event-overlay lane |
| `bar_track` / `bar_track_border` | a row bar's track |

| Identity and geometry | |
| --- | --- |
| `name` / `label` | `"duxa"` \| `"velious"` \| `"ledger"`, and its display name |
| `base_font_size` | the user's font size — every scale multiplies this |
| `frame_opacity` | 0..1, the **frame only** |
| `row_style` | `"stacked"` or `"full"` (Ledger) |
| `plate_padding` / `notch` | the frame gap and the Velious corner cut, px |
| `overlay_bar_height` | an on-game countdown bar's height, px |

Methods: `px(scale, minimum=7)`, `tracking(scale, em)`,
`typography(role, *, color=None)`, `frame_inset()`, `gradient(stops)`,
`bar_fill(color)`, `bar_stylesheet(color)`, `overlay_bar_stylesheet(color)`,
`overlay_stylesheet()`, `config_stylesheet()`.

Module level: `current()`, `SKIN_NAMES`, `TypographyRole`, `SMALL_DISPLAY`,
`BODY_TEXT`, `NUMERIC_TEXT`, `px`, `tracking`, `typography_style`, `TITLE`,
`ROW_NAME`, `ROW_VALUE`, `GOOD`, `BAD`, `COOLDOWN`, `TIMER`, `ROLL`,
`POP_WINDOW`, `LINK`, `shade`, `rgba`, `gradient`.

## What is deliberately not exposed

The internal `Skin` object, its fifty-odd tokens, and the stylesheet builders
around them. They churn with every design pass, and SDK 1.x is additive-only —
anything exported here is frozen for the entire major version. If you need
something the façade does not carry,
[open an issue](https://github.com/prokopto-dev/nparse-plus/issues) rather
than reaching into `nparseplus.ui`; the façade is the supported path and it
can grow.

## See also

- [`examples/plugins/merchant_prices/window.py`](https://github.com/prokopto-dev/nparse-plus/tree/master/examples/plugins/merchant_prices/window.py)
  — the reference implementation of everything above.
- [API reference](api.md#host-re-export-modules-lazy) — the exported names.
- [Appearance settings](../settings/appearance.md) — what the user is choosing
  between.
