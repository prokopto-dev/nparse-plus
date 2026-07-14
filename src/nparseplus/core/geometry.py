"""Small geometry types shared by parsers, maps, and the network layer."""

from typing import NamedTuple


class Loc(NamedTuple):
    """An EQ /loc coordinate triple (as reported by the client: y, x, z order
    is normalized to x, y, z here — parsers do the swap)."""

    x: float
    y: float
    z: float
