"""Inventory vocabulary — the closed sets an equipment row is described with.

Pure stdlib. Shared by the entity (invariants), the API schemas (validation)
and the OpenAPI document, so the three never disagree on what a valid row is.
"""

from __future__ import annotations

from typing import Final, Literal

# Usable or broken — the two states a crew cares about when picking tools for a day.
InventoryCondition = Literal["working", "damaged"]
INVENTORY_CONDITIONS: Final[frozenset[str]] = frozenset({"working", "damaged"})

# Kept at a company warehouse (with its address) or currently out on a site (a project).
InventoryLocationType = Literal["warehouse", "site"]
INVENTORY_LOCATION_TYPES: Final[frozenset[str]] = frozenset({"warehouse", "site"})

# Canonical category slugs; labels live in the clients' locale files.
INVENTORY_CATEGORY_SLUGS: Final[tuple[str, ...]] = (
    "power_tool",
    "hand_tool",
    "measuring",
    "access",
    "safety",
    "machine",
    "other",
)
InventoryCategory = Literal["power_tool", "hand_tool", "measuring", "access", "safety", "machine", "other"]


def is_valid_inventory_category(value: str) -> bool:
    return value in INVENTORY_CATEGORY_SLUGS
