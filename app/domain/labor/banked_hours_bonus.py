"""Banked supplement hours converted into paid bonus days.

Extra hours worked beyond a shift are banked, not paid by the hour: every 8 banked
hours earn a full paid day, and a remainder of 4 hours or more earns a half day.
Anything below 4 stays in the bank.

Centralised here so the per-worker summary and the monthly rollup price the bonus
identically — a worker who reads one screen then the other must not see two different
earned totals for the same month.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

#: Banked hours that make up one full paid day.
HOURS_PER_BONUS_DAY = 8


@dataclass(frozen=True, slots=True)
class BankedHoursBonus:
    """Bonus days earned from banked hours, priced at ``daily_rate``."""

    full_days: int
    half_days: int
    cost: Decimal

    @property
    def days(self) -> Decimal:
        """Bonus days as a fraction — a half day counts 0.5."""
        return Decimal(self.full_days) + Decimal(self.half_days) * Decimal("0.5")


def bonus_for_banked_hours(banked_hours: int, daily_rate: Decimal) -> BankedHoursBonus:
    """Price ``banked_hours`` at ``daily_rate``. Negative or missing hours earn nothing."""
    banked = max(banked_hours or 0, 0)
    full_days = banked // HOURS_PER_BONUS_DAY
    half_days = 1 if (banked % HOURS_PER_BONUS_DAY) >= HOURS_PER_BONUS_DAY // 2 else 0
    cost = Decimal(full_days) * daily_rate + Decimal(half_days) * daily_rate * Decimal("0.5")
    return BankedHoursBonus(full_days=full_days, half_days=half_days, cost=cost)
