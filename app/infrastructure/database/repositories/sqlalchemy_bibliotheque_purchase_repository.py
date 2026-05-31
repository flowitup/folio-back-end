"""SQLAlchemy adapter implementing ILibraryPurchaseRepository."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.value_objects.library_purchase import LibraryPurchase
from app.infrastructure.database.models.bibliotheque_purchase import BibliothequePurchaseModel


class SqlAlchemyBibliothequePurchaseRepository:
    """Implements ILibraryPurchaseRepository against a SQLAlchemy session.

    Idempotency contract: add_if_absent guarantees that re-importing the same
    (product_id, source_document_ref, line_index) triple inserts zero rows and
    returns False. The import use-case MUST NOT apply aggregate updates when
    False is returned.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def add_if_absent(self, purchase: LibraryPurchase) -> bool:
        """Insert purchase if the idempotency key is new; return True when inserted.

        Uses find-then-insert inside a SAVEPOINT so that a duplicate on
        PostgreSQL or a unique-constraint violation on SQLite (tests) can both
        be handled without aborting the outer transaction.
        """
        # Check first to avoid exception overhead on the common re-import path.
        existing = self._session.execute(
            select(BibliothequePurchaseModel).where(
                BibliothequePurchaseModel.product_id == purchase.product_id,
                BibliothequePurchaseModel.source_document_ref == purchase.source_document_ref,
                BibliothequePurchaseModel.line_index == purchase.line_index,
            )
        ).scalar_one_or_none()

        if existing is not None:
            return False

        # Use a SAVEPOINT so a race-condition duplicate (two concurrent imports)
        # does not abort the outer transaction — the nested block rolls back only
        # the SAVEPOINT on IntegrityError.
        try:
            with self._session.begin_nested():
                orm = BibliothequePurchaseModel.from_vo(purchase)
                self._session.add(orm)
                self._session.flush()
        except IntegrityError:
            # Lost the race — another concurrent request already inserted this row.
            return False

        return True

    def list_by_product(self, product_id: UUID) -> list[LibraryPurchase]:
        """Return all purchases for a product ordered by purchased_at DESC."""
        rows = (
            self._session.execute(
                select(BibliothequePurchaseModel)
                .where(BibliothequePurchaseModel.product_id == product_id)
                .order_by(BibliothequePurchaseModel.purchased_at.desc())
            )
            .scalars()
            .all()
        )
        return [r.to_vo() for r in rows]
