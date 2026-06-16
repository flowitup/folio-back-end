"""WorkerRateChange domain entity.

Pure dataclass — no Flask/SQLAlchemy imports.
Represents an effective-dated pay-rate override for a worker.
The applicable rate on date D for worker W is the row with the
greatest effective_date <= D; if none, fall back to worker.daily_rate.
"""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from app.domain.exceptions.labor_exceptions import InvalidRateChangeError


@dataclass(slots=True)
class WorkerRateChange:
    """Effective-dated daily-rate change for a worker.

    Invariant: daily_rate must be > 0 (enforced in __post_init__).
    Equality and hashing are by ``id`` so that domain collections
    deduplicate by identity — mirrors the Worker entity pattern.
    """

    id: UUID
    worker_id: UUID
    effective_date: date
    daily_rate: Decimal
    created_at: datetime

    def __post_init__(self) -> None:
        if self.daily_rate is None or self.daily_rate <= 0:
            raise InvalidRateChangeError("daily_rate must be > 0")

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, WorkerRateChange):
            return NotImplemented
        return self.id == other.id

    def __hash__(self) -> int:
        return hash(self.id)
