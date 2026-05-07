"""SQLAlchemy adapter implementing BillingNumberCounterRepositoryPort.

On Postgres: next_value() uses an atomic INSERT ... ON CONFLICT DO UPDATE RETURNING
(upsert) that collapses the select-or-insert into a single statement, eliminating the
race window that SELECT FOR UPDATE has on *first insert* (FOR UPDATE cannot lock a row
that does not yet exist, causing IntegrityError when two concurrent transactions both
see row=None and both try to INSERT).

On SQLite (unit/integration tests): falls back to the legacy SELECT FOR UPDATE path.
SQLite does not support row-level locking so concurrent tests are meaningless anyway,
and the simple path keeps test fakes working without dialect-specific SQL.

Schema change (phase 03 — companies module):
  Old PK key: (user_id, kind, year)   → user_id arg passed to next_value()
  New PK key: (company_id, kind, year) → company_id arg passed to next_value()
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

    def next_value(self, company_id: UUID, kind: BillingDocumentKind, year: int) -> int:
        """Return the next sequence value (1-based, monotonically increasing).

        Postgres path (atomic, race-free):
          Uses INSERT ... ON CONFLICT DO UPDATE ... RETURNING in a single statement.
          The claimed value is (returned next_value - 1) because the upsert stores
          the *next* value to issue on the next call (next_value=2 on first insert,
          meaning sequence 1 is claimed; next_value increments by 1 on conflict).

        SQLite fallback (best-effort, single-threaded tests):
          SELECT FOR UPDATE → insert-or-increment. Not race-safe under concurrent
          load, which is acceptable for tests (SQLite has no row-level locking).

        The session must be inside an active transaction at call time; the
        caller's commit finalises the lock release.
        """
        try:
            bind = self._session.get_bind()
            dialect = bind.dialect.name
        except Exception:  # noqa: BLE001
            dialect = "sqlite"  # safe fallback for test environments

        if dialect == "postgresql":
            return self._next_value_postgres(company_id, kind, year)
        return self._next_value_sqlite_fallback(company_id, kind, year)

    def _next_value_postgres(self, company_id: UUID, kind: BillingDocumentKind, year: int) -> int:
        """Atomic Postgres upsert — eliminates first-insert race."""
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        stmt = (
            pg_insert(BillingNumberCounterModel)
            .values(company_id=company_id, kind=kind.value, year=year, next_value=2)
            .on_conflict_do_update(
                index_elements=["company_id", "kind", "year"],
                set_={"next_value": BillingNumberCounterModel.next_value + 1},
            )
            .returning(BillingNumberCounterModel.next_value)
        )
        new_next = self._session.execute(stmt).scalar_one()
        # new_next is the value now stored; the value claimed is one less.
        return new_next - 1

    def _next_value_sqlite_fallback(self, company_id: UUID, kind: BillingDocumentKind, year: int) -> int:
        """Legacy SELECT FOR UPDATE path — SQLite / test environments only."""
        stmt = (
            select(BillingNumberCounterModel)
            .where(
                BillingNumberCounterModel.company_id == company_id,
                BillingNumberCounterModel.kind == kind.value,
                BillingNumberCounterModel.year == year,
            )
            .with_for_update()
        )
        row = self._session.execute(stmt).scalar_one_or_none()

        if row is None:
            # First sequence number is 1; store 2 as the *next* value to return.
            row = BillingNumberCounterModel(
                company_id=company_id,
                kind=kind.value,
                year=year,
                next_value=2,
            )
            self._session.add(row)
            self._session.flush()
            return 1

        # Read, advance, and return the value that was claimed.
        current = row.next_value
        row.next_value = current + 1
        self._session.flush()
        return current
