"""SQLAlchemy adapter implementing IPaymentMethodRepository.

All public methods satisfy the Protocol defined in
``app.application.payment_methods.ports.IPaymentMethodRepository``.

Converter helpers ``_to_entity`` and ``_to_model`` are private to this module
and must never be imported directly from outside.
"""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain.payment_methods.payment_method import PaymentMethod
from app.infrastructure.database.models.invoice import InvoiceModel
from app.infrastructure.database.models.payment_method import PaymentMethodModel


# ---------------------------------------------------------------------------
# Private converters
# ---------------------------------------------------------------------------


def _to_entity(row: PaymentMethodModel) -> PaymentMethod:
    """Convert a PaymentMethodModel ORM row to a domain PaymentMethod entity."""
    return PaymentMethod(
        id=row.id,
        company_id=row.company_id,
        label=row.label,
        is_builtin=row.is_builtin,
        is_active=row.is_active,
        created_by=row.created_by,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _to_model(method: PaymentMethod, row: PaymentMethodModel) -> PaymentMethodModel:
    """Write domain entity fields onto an existing (or new) ORM row in-place.

    Returns the same ``row`` instance for convenience.
    """
    row.id = method.id
    row.company_id = method.company_id
    row.label = method.label
    row.is_builtin = method.is_builtin
    row.is_active = method.is_active
    row.created_by = method.created_by
    row.created_at = method.created_at
    row.updated_at = method.updated_at
    return row


# ---------------------------------------------------------------------------
# Repository
# ---------------------------------------------------------------------------


class SqlAlchemyPaymentMethodRepository:
    """Implements IPaymentMethodRepository against a SQLAlchemy session."""

    def __init__(self, session: Session) -> None:
        self._session = session

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def find_by_id(self, id: UUID) -> Optional[PaymentMethod]:
        """Return a payment method by UUID, or None if not found."""
        row = self._session.get(PaymentMethodModel, id)
        return _to_entity(row) if row is not None else None

    def find_by_id_for_update(self, id: UUID) -> Optional[PaymentMethod]:
        """Return a payment method with SELECT FOR UPDATE lock, or None."""
        stmt = select(PaymentMethodModel).where(PaymentMethodModel.id == id).with_for_update()
        row = self._session.execute(stmt).scalar_one_or_none()
        return _to_entity(row) if row is not None else None

    def find_active_by_company(self, company_id: UUID) -> list[PaymentMethod]:
        """Return all active methods for a company, ordered by label."""
        stmt = (
            select(PaymentMethodModel)
            .where(
                PaymentMethodModel.company_id == company_id,
                PaymentMethodModel.is_active.is_(True),
            )
            .order_by(PaymentMethodModel.label)
        )
        rows = self._session.execute(stmt).scalars().all()
        return [_to_entity(r) for r in rows]

    def find_all_by_company(self, company_id: UUID, *, include_inactive: bool = False) -> list[PaymentMethod]:
        """Return methods for a company, optionally including inactive rows."""
        stmt = select(PaymentMethodModel).where(PaymentMethodModel.company_id == company_id)
        if not include_inactive:
            stmt = stmt.where(PaymentMethodModel.is_active.is_(True))
        stmt = stmt.order_by(PaymentMethodModel.label)
        rows = self._session.execute(stmt).scalars().all()
        return [_to_entity(r) for r in rows]

    def find_by_label_ci(self, company_id: UUID, label: str, *, only_active: bool = True) -> Optional[PaymentMethod]:
        """Return a method matching *label* case-insensitively, or None.

        The case-insensitive comparison uses ``func.lower`` on both sides so it
        works with both PostgreSQL and SQLite (used in tests).
        """
        stmt = select(PaymentMethodModel).where(
            PaymentMethodModel.company_id == company_id,
            func.lower(PaymentMethodModel.label) == func.lower(label),
        )
        if only_active:
            stmt = stmt.where(PaymentMethodModel.is_active.is_(True))
        row = self._session.execute(stmt).scalar_one_or_none()
        return _to_entity(row) if row is not None else None

    def count_invoices_referencing(self, payment_method_id: UUID) -> int:
        """Return the number of invoices that reference *payment_method_id*."""
        stmt = select(func.count()).where(InvoiceModel.payment_method_id == payment_method_id)
        result: int = self._session.execute(stmt).scalar_one()
        return result

    def find_all_by_company_with_usage_count(
        self, company_id: UUID, *, include_inactive: bool = False
    ) -> list[tuple[PaymentMethod, int]]:
        """Return (PaymentMethod, usage_count) pairs for a company in a single query.

        Uses LEFT JOIN + GROUP BY to avoid the N+1 pattern of calling
        ``count_invoices_referencing`` per method.
        """
        pm = PaymentMethodModel
        inv = InvoiceModel

        stmt = (
            select(pm, func.count(inv.id).label("usage_count"))
            .outerjoin(inv, inv.payment_method_id == pm.id)
            .where(pm.company_id == company_id)
        )
        if not include_inactive:
            stmt = stmt.where(pm.is_active.is_(True))
        stmt = stmt.group_by(pm.id).order_by(pm.label)

        rows = self._session.execute(stmt).all()
        return [(_to_entity(row[0]), int(row[1])) for row in rows]

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def save(self, method: PaymentMethod) -> PaymentMethod:
        """Insert or update a payment method. Returns the persisted instance."""
        row = self._session.get(PaymentMethodModel, method.id)
        if row is None:
            row = PaymentMethodModel()
            _to_model(method, row)
            self._session.add(row)
        else:
            _to_model(method, row)
        self._session.flush()
        return _to_entity(row)

    def insert_many(self, methods: list[PaymentMethod]) -> None:
        """Bulk-insert multiple payment methods (no upsert — rows must be new)."""
        for method in methods:
            row = PaymentMethodModel()
            _to_model(method, row)
            self._session.add(row)
        self._session.flush()
