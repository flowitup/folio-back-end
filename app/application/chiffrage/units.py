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

# Ordering step for postes/articles, mirroring app.application.task.use_cases.
# Large gaps let a drop between two neighbours land on a free integer without
# renumbering the rest of the list.
POSITION_STEP = 1000


def is_preset_unit(symbol: str) -> bool:
    """Return True if *symbol* is one of the built-in units."""
    return symbol in PRESET_UNITS
