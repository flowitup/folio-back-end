"""SQLAlchemy adapter implementing ISupplierRepository."""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.entities.supplier import Supplier
from app.infrastructure.database.models.bibliotheque_supplier import BibliothequeSupplierModel


class SqlAlchemyBibliothequeSupplierRepository:
    """Implements ISupplierRepository against a SQLAlchemy session."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_or_create(self, supplier: Supplier) -> Supplier:
        """Return existing supplier for (company_id, slug), or insert and return new one.

        Uses find-then-insert inside the existing session rather than ON CONFLICT
        so this works correctly with both PostgreSQL and SQLite (test env).
        """
        existing = self.find_by_slug(supplier.company_id, supplier.slug)
        if existing is not None:
            return existing
        orm = BibliothequeSupplierModel.from_entity(supplier)
        self._session.add(orm)
        self._session.flush()
        return orm.to_entity()

    def list_by_company(self, company_id: UUID) -> list[Supplier]:
        """Return all suppliers for a company ordered by name."""
        rows = (
            self._session.execute(
                select(BibliothequeSupplierModel)
                .where(BibliothequeSupplierModel.company_id == company_id)
                .order_by(BibliothequeSupplierModel.name)
            )
            .scalars()
            .all()
        )
        return [r.to_entity() for r in rows]

    def find_by_id(self, supplier_id: UUID) -> Optional[Supplier]:
        """Return supplier by UUID, or None."""
        row = self._session.get(BibliothequeSupplierModel, supplier_id)
        return row.to_entity() if row is not None else None

    def find_by_slug(self, company_id: UUID, slug: str) -> Optional[Supplier]:
        """Return supplier by (company_id, slug), or None."""
        row = self._session.execute(
            select(BibliothequeSupplierModel).where(
                BibliothequeSupplierModel.company_id == company_id,
                BibliothequeSupplierModel.slug == slug,
            )
        ).scalar_one_or_none()
        return row.to_entity() if row is not None else None
