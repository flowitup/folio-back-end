"""SQLAlchemy adapter implementing BillingNumberCounterRepositoryPort.

next_value() uses SELECT FOR UPDATE to guarantee atomicity under concurrent
document creates. On SQLite (tests) the FOR UPDATE hint is silently dropped
by SQLAlchemy — single-threaded tests remain correct, concurrent SQLite tests
are not meaningful (SQLite does not support row-level locking).
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.billing.enums import BillingDocumentKind
from app.infrastructure.database.models.billing_number_counter import (
    BillingNumberCounterModel,
)


class SqlAlchemyBillingNumberCounterRepository:
    """Implements BillingNumberCounterRepositoryPort against a SQLAlchemy session."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def next_value(self, user_id: UUID, kind: BillingDocumentKind, year: int) -> int:
        """Return the next sequence value (1-based, monotonically increasing).

        Algorithm:
          1. Lock the counter row with SELECT FOR UPDATE (prevents concurrent
             readers from reading the same value before it is incremented).
          2. If no row exists, insert one with next_value=1 and return 1.
          3. Otherwise read current next_value, increment in-place, flush, return.

        The session must be inside an active transaction at call time; the
        caller's commit finalises the lock release.
        """
        stmt = (
            select(BillingNumberCounterModel)
            .where(
                BillingNumberCounterModel.user_id == user_id,
                BillingNumberCounterModel.kind == kind.value,
                BillingNumberCounterModel.year == year,
            )
            .with_for_update()
        )
        row = self._session.execute(stmt).scalar_one_or_none()

        if row is None:
            row = BillingNumberCounterModel(
                user_id=user_id,
                kind=kind.value,
                year=year,
                next_value=1,
            )
            self._session.add(row)
            self._session.flush()
            return 1

        current = row.next_value
        row.next_value = current + 1
        self._session.flush()
        return current
