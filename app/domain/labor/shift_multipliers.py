"""Shift-type multipliers for labor cost computation.

Centralised here so both the domain entity's effective_cost() method and the
application-layer list/summary use cases resolve multipliers from a single source
of truth.
"""

SHIFT_MULTIPLIERS: dict[str, float] = {
    "full": 1.0,
    "half": 0.5,
    "overtime": 1.5,
}
