"""SQLAlchemy adapter implementing ILibraryProductRepository."""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.entities.library_product import LibraryProduct
from app.infrastructure.database.models.bibliotheque_product import BibliothequeProductModel


class SqlAlchemyBibliothequeProductRepository:
    """Implements ILibraryProductRepository against a SQLAlchemy session."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def upsert(self, product: LibraryProduct) -> LibraryProduct:
        """Insert or update a product row. Returns the persisted instance.

        New-product inserts run inside a SAVEPOINT so that a concurrent insert
        of the same (company_id, supplier_id, supplier_reference) triple does not
        abort the outer import transaction. On IntegrityError the savepoint rolls
        back and we re-find the row that won the race, then apply the update path.
        """
        row = self._session.get(BibliothequeProductModel, product.id)
        if row is None:
            try:
                with self._session.begin_nested():
                    row = BibliothequeProductModel.from_entity(product)
                    self._session.add(row)
                    self._session.flush()
            except IntegrityError:
                # Another concurrent request inserted the same (company, supplier, reference).
                # Re-find by the unique business key and fall through to the update path.
                row = self._session.execute(
                    select(BibliothequeProductModel).where(
                        BibliothequeProductModel.company_id == product.company_id,
                        BibliothequeProductModel.supplier_id == product.supplier_id,
                        BibliothequeProductModel.supplier_reference == product.supplier_reference,
                    )
                ).scalar_one()
                row.update_from_entity(product)
                self._session.flush()
        else:
            row.update_from_entity(product)
            self._session.flush()
        return row.to_entity()

    def find_by_reference(self, company_id: UUID, supplier_id: UUID, reference: str) -> Optional[LibraryProduct]:
        """Return product by (company_id, supplier_id, supplier_reference), or None."""
        row = self._session.execute(
            select(BibliothequeProductModel).where(
                BibliothequeProductModel.company_id == company_id,
                BibliothequeProductModel.supplier_id == supplier_id,
                BibliothequeProductModel.supplier_reference == reference,
            )
        ).scalar_one_or_none()
        return row.to_entity() if row is not None else None

    def find_by_id(self, product_id: UUID) -> Optional[LibraryProduct]:
        """Return product by UUID, or None."""
        row = self._session.get(BibliothequeProductModel, product_id)
        return row.to_entity() if row is not None else None

    def find_by_id_for_update(self, product_id: UUID) -> Optional[LibraryProduct]:
        """Return product by UUID with SELECT FOR UPDATE, or None.

        Serializes concurrent imports on the same product row so aggregate
        updates (purchase_count, total_quantity, last_unit_price) cannot race.
        Degrades gracefully to a plain SELECT on SQLite (tests), which has no
        concurrent transactions and therefore no need for row-level locking.
        """
        row = self._session.execute(
            select(BibliothequeProductModel).where(BibliothequeProductModel.id == product_id).with_for_update()
        ).scalar_one_or_none()
        return row.to_entity() if row is not None else None

    def list(
        self,
        company_id: UUID,
        *,
        supplier_id: Optional[UUID] = None,
        category: Optional[str] = None,
        q: Optional[str] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[LibraryProduct], int]:
        """Return paginated products and total count matching the filters.

        Search (q) matches against name, description, and supplier_reference
        using ILIKE (case-insensitive substring). Ordered by last_purchased_at
        DESC NULLS LAST so recently active products surface first.
        """
        base = select(BibliothequeProductModel).where(BibliothequeProductModel.company_id == company_id)
        if supplier_id is not None:
            base = base.where(BibliothequeProductModel.supplier_id == supplier_id)
        if category is not None:
            base = base.where(BibliothequeProductModel.category == category)
        if q:
            pattern = f"%{q}%"
            base = base.where(
                BibliothequeProductModel.name.ilike(pattern)
                | BibliothequeProductModel.description.ilike(pattern)
                | BibliothequeProductModel.supplier_reference.ilike(pattern)
            )

        total = self._session.execute(select(func.count()).select_from(base.subquery())).scalar_one()

        rows = (
            self._session.execute(
                base.order_by(BibliothequeProductModel.last_purchased_at.desc().nulls_last())
                .limit(limit)
                .offset(offset)
            )
            .scalars()
            .all()
        )
        return [r.to_entity() for r in rows], total

    def distinct_categories(self, company_id: UUID) -> list[str]:
        """Return sorted distinct non-null category values for the company."""
        rows = (
            self._session.execute(
                select(BibliothequeProductModel.category)
                .where(
                    BibliothequeProductModel.company_id == company_id,
                    BibliothequeProductModel.category.is_not(None),
                )
                .distinct()
                .order_by(BibliothequeProductModel.category)
            )
            .scalars()
            .all()
        )
        return list(rows)
