"""SeedPaymentMethodsForCompanyUseCase — inserts builtin methods for a new company.

Called by CreateCompanyUseCase (phase 03 wiring) immediately after a new
company is persisted. Also safe to call from the Alembic migration for
existing companies (idempotent: skips if methods already exist).

Builtin seed set:
  1. "Cash"           — always inserted.
  2. ``<legal_name>`` — inserted only when legal_name is not None, not blank,
                        and differs from "Cash" (case-insensitive).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from uuid import UUID, uuid4

from app.application.payment_methods.ports import IPaymentMethodRepository, TransactionalSessionPort
from app.domain.payment_methods.payment_method import PaymentMethod


class SeedPaymentMethodsForCompanyUseCase:
    """Insert the two builtin payment methods for a newly-created company.

    Idempotent: if the company already has at least one payment method the
    seed is skipped entirely. This prevents double-seeding when the migration
    and the create-company hook both run (migration runs first on existing
    companies; hook runs only for new ones).
    """

    def __init__(self, payment_method_repo: IPaymentMethodRepository) -> None:
        self._repo = payment_method_repo

    def execute(
        self,
        company_id: UUID,
        legal_name: Optional[str],
        created_by: Optional[UUID],
        db_session: TransactionalSessionPort,
    ) -> None:
        # Idempotency guard: if any methods already exist, skip.
        existing = self._repo.find_all_by_company(company_id, include_inactive=True)
        if existing:
            return

        now = datetime.now(timezone.utc)
        methods_to_insert: list[PaymentMethod] = []

        # 1. Cash — always builtin
        methods_to_insert.append(
            PaymentMethod(
                id=uuid4(),
                company_id=company_id,
                label="Cash",
                is_builtin=True,
                is_active=True,
                created_by=created_by,
                created_at=now,
                updated_at=now,
            )
        )

        # 2. Company legal name — only when distinct from "Cash"
        normalised_name = legal_name.strip() if legal_name else None
        if normalised_name and normalised_name.lower() != "cash":
            methods_to_insert.append(
                PaymentMethod(
                    id=uuid4(),
                    company_id=company_id,
                    label=normalised_name,
                    is_builtin=True,
                    is_active=True,
                    created_by=created_by,
                    created_at=now,
                    updated_at=now,
                )
            )

        with db_session.begin_nested():
            self._repo.insert_many(methods_to_insert)

        db_session.commit()
