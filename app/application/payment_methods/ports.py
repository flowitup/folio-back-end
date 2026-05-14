"""Repository and session ports (Protocols) for the payment_methods application layer.

All protocols are structural (no runtime_checkable) — type-checked only.
Infrastructure implementations live in
``app/infrastructure/database/repositories/sqlalchemy_payment_method_repository.py``.

No Flask, SQLAlchemy, or any infrastructure imports are permitted in this file.
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from typing import Any, Protocol
from uuid import UUID

from app.domain.payment_methods.payment_method import PaymentMethod


class IPaymentMethodRepository(Protocol):
    """Persistence contract for PaymentMethod aggregates."""

    def find_by_id(self, id: UUID) -> PaymentMethod | None:
        """Return a payment method by UUID, or None if not found."""
        ...

    def find_by_id_for_update(self, id: UUID) -> PaymentMethod | None:
        """Return a payment method with SELECT FOR UPDATE lock, or None."""
        ...

    def find_active_by_company(self, company_id: UUID) -> list[PaymentMethod]:
        """Return all active (is_active=True) methods for a company, ordered by label."""
        ...

    def find_all_by_company(self, company_id: UUID, *, include_inactive: bool = False) -> list[PaymentMethod]:
        """Return all methods for a company.

        When ``include_inactive`` is False (default), only active rows are
        returned. Set True for admin views that need to show soft-deleted rows.
        """
        ...

    def find_by_label_ci(self, company_id: UUID, label: str, *, only_active: bool = True) -> PaymentMethod | None:
        """Return a method matching *label* case-insensitively, or None.

        When ``only_active`` is True (default), only active rows are compared.
        Used to enforce the duplicate-label guard before INSERT.
        """
        ...

    def save(self, method: PaymentMethod) -> PaymentMethod:
        """Insert or update a payment method. Returns the persisted instance."""
        ...

    def insert_many(self, methods: list[PaymentMethod]) -> None:
        """Bulk-insert multiple payment methods.

        Used by ``SeedPaymentMethodsForCompanyUseCase`` which inserts 1-2 rows
        atomically. No partial-update semantics — rows must not already exist.
        """
        ...

    def count_invoices_referencing(self, payment_method_id: UUID) -> int:
        """Return how many invoices reference *payment_method_id*.

        Used to populate ``usage_count`` on ``PaymentMethodResponse`` so the
        delete-confirm UX can warn the user about historical references.
        """
        ...


class RoleCheckerPort(Protocol):
    """Minimal port to ask whether a user holds a specific permission.

    Mirrors the equivalent port in ``app.application.companies.ports``.
    """

    def has_permission(self, user_id: UUID, permission: str) -> bool:
        """Return True if user_id holds *permission* (or '*:*')."""
        ...


class TransactionalSessionPort(Protocol):
    """Minimal session contract for mutating payment_methods use-cases.

    Mirrors the equivalent port defined in ``app.application.companies.ports``
    so the infrastructure layer can wire the same ``db.session`` to both.
    """

    def begin_nested(self) -> AbstractContextManager[Any]:
        """Open a SAVEPOINT block as a context manager."""
        ...

    def commit(self) -> None:
        """Commit the outer transaction."""
        ...

    def flush(self) -> None:
        """Flush pending changes to the DB without committing."""
        ...
