"""EQTool-style continuous z-axis fading (pure math, Qt-free).

Port of EQTool's MapOpacityHelper.AdjustOpacity: map geometry fades out
smoothly as its z distance from the player grows, using the per-zone
``zone_level_height`` from the zone database.
"""

from __future__ import annotations

import math

MIN_OPACITY = 0.1
DEFAULT_BAND_WIDTH = 10.0


def fade_opacity(distance: float, zone_level_height: int | None) -> float:
    """Opacity for a map element ``distance`` z-units away from the player.

    Exact port of EQTool's MapOpacityHelper.AdjustOpacity curve, with
    ``h = zone_level_height``:

    - ``d < h``: fully opaque (1.0)
    - ``h <= d <= 3h``: ``((2h) - (d - h)) / (2h) + 0.1`` clamped to [0.1, 1.0]
    - ``d > 3h``: 0.1

    A falsy ``zone_level_height`` means the zone has no level metadata, so
    nothing fades (1.0).
    """
    if not zone_level_height:
        return 1.0
    h = float(zone_level_height)
    d = abs(distance)
    if d < h:
        return 1.0
    if d <= 3.0 * h:
        value = ((2.0 * h) - (d - h)) / (2.0 * h) + MIN_OPACITY
        return min(max(value, MIN_OPACITY), 1.0)
    return MIN_OPACITY


def band_width_for(zone_level_height: int | None) -> float:
    """Width of the fine z-bands used to group map lines for fading."""
    if not zone_level_height:
        return DEFAULT_BAND_WIDTH
    return max(float(zone_level_height), 10.0) / 2.0


def band_key_for(z: float, band_width: float) -> int:
    """Stable integer key of the band containing ``z``."""
    return math.floor(z / band_width)


def band_center(key: int, band_width: float) -> float:
    """Center z value of the band with the given key."""
    return (key + 0.5) * band_width
