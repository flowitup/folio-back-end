"""Unit-of-measure vocabulary for chiffrage articles.

The preset symbols are a frozen constant rather than seeded rows: they are the
same for every project, need no migration to extend, and cannot be deleted by a
user into an inconsistent state. Projects may add their own symbols on top
(ChiffrageUnit); the union of the two is the allowed set enforced at the API
boundary, which is what makes the front-end dropdown authoritative instead of
decorative.

Symbols are display-neutral (no translation): "m²" reads the same in en/fr/vi.
"""

from __future__ import annotations

# Ordered by how often they come up on a French chantier.
PRESET_UNITS: tuple[str, ...] = (
    "u",
    "ml",
    "m",
    "m²",
    "m³",
    "kg",
    "L",
    "sac",
    "boîte",
    "rouleau",
    "lot",
    "forfait",
)


def unit_key(symbol: str) -> str:
    """Comparison key for unit symbols: case-insensitive, 'm2'/'m3' read as 'm²'/'m³'.

    'U' is the preset 'u', 'Sac' is 'sac', 'M2' is 'm²': two symbols with the
    same key are the same unit and must not both be offered.
    """
    key = symbol.strip().casefold()
    return key.replace("2", "²").replace("3", "³") if key in ("m2", "m3") else key


PRESET_BY_KEY: dict[str, str] = {unit_key(u): u for u in PRESET_UNITS}

# Ordering step for postes/articles, mirroring app.application.task.use_cases.
# Large gaps let a drop between two neighbours land on a free integer without
# renumbering the rest of the list.
POSITION_STEP = 1000
