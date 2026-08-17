"""Quote write use-cases: create, update, delete, select."""

from __future__ import annotations

from decimal import Decimal
from typing import Optional
from uuid import UUID

from app.application.chiffrage.exceptions import ArticleNotFoundError
from app.application.chiffrage.ports import ChiffrageRepositoryPort, TransactionalSessionPort
from app.application.chiffrage.validation import (
    clean_optional_text,
    owned_article,
    owned_quote,
    require_supplier,
    validate_price,
    validate_tva_rate,
)
from app.domain.entities.chiffrage_quote import ChiffrageQuote


class CreateQuoteUseCase:
    """Record a fournisseur's price for an article."""

    def __init__(self, repo: ChiffrageRepositoryPort, db_session: TransactionalSessionPort) -> None:
        self._repo = repo
        self._db = db_session

    def execute(
        self,
        *,
        project_id: UUID,
        article_id: UUID,
        unit_price_ht: Decimal,
        tva_rate: Decimal,
        supplier_id: Optional[UUID] = None,
        supplier_name: Optional[str] = None,
        library_product_id: Optional[UUID] = None,
        product_url: Optional[str] = None,
        note: Optional[str] = None,
    ) -> ChiffrageQuote:
        owned_article(self._repo, article_id, project_id)
        quote = ChiffrageQuote.create(
            article_id=article_id,
            unit_price_ht=validate_price(unit_price_ht),
            tva_rate=validate_tva_rate(tva_rate),
            supplier_id=supplier_id,
            supplier_name=require_supplier(supplier_id, supplier_name),
            library_product_id=library_product_id,
            product_url=clean_optional_text(product_url),
            note=clean_optional_text(note),
        )
        self._repo.add_quote(quote)
        self._db.commit()
        return quote


class UpdateQuoteUseCase:
    """Edit a quote's fournisseur, price, VAT rate, link or note."""

    def __init__(self, repo: ChiffrageRepositoryPort, db_session: TransactionalSessionPort) -> None:
        self._repo = repo
        self._db = db_session

    def execute(
        self,
        *,
        project_id: UUID,
        quote_id: UUID,
        supplier_id: object,
        supplier_name: object,
        library_product_id: object,
        unit_price_ht: object,
        tva_rate: object,
        product_url: object,
        note: object,
    ) -> ChiffrageQuote:
        quote = owned_quote(self._repo, quote_id, project_id)
        U = ChiffrageQuote._UNSET

        # Resolve the post-update supplier pair before validating it: clearing
        # one half while the other stays set must remain legal, clearing both
        # must not.
        next_supplier_id = quote.supplier_id if supplier_id is U else supplier_id
        next_supplier_name = quote.supplier_name if supplier_name is U else supplier_name
        validated_name = require_supplier(
            next_supplier_id if isinstance(next_supplier_id, UUID) or next_supplier_id is None else None,
            None if next_supplier_name is None else str(next_supplier_name),
        )

        updated = quote.with_updates(
            supplier_id=(U if supplier_id is U else supplier_id),
            supplier_name=(U if supplier_name is U else validated_name),
            library_product_id=(U if library_product_id is U else library_product_id),
            unit_price_ht=(U if unit_price_ht is U else validate_price(Decimal(str(unit_price_ht)))),
            tva_rate=(U if tva_rate is U else validate_tva_rate(Decimal(str(tva_rate)))),
            product_url=(
                U if product_url is U else clean_optional_text(None if product_url is None else str(product_url))
            ),
            note=(U if note is U else clean_optional_text(None if note is None else str(note))),
        )
        self._repo.save_quote(updated)
        self._db.commit()
        return updated


class DeleteQuoteUseCase:
    """Delete a quote.

    No selection repair is needed: with the retained offer flagged in-row, the
    article simply falls back to the cheapest remaining quote.
    """

    def __init__(self, repo: ChiffrageRepositoryPort, db_session: TransactionalSessionPort) -> None:
        self._repo = repo
        self._db = db_session

    def execute(self, *, project_id: UUID, quote_id: UUID) -> None:
        owned_quote(self._repo, quote_id, project_id)
        self._repo.delete_quote(quote_id)
        self._db.commit()


class SelectQuoteUseCase:
    """Mark a quote as the retained offer for its article.

    The article row is locked first so two concurrent selections on the same
    article serialise; otherwise both could clear-then-set and leave two quotes
    flagged, which would make the budget total depend on row order.
    """

    def __init__(self, repo: ChiffrageRepositoryPort, db_session: TransactionalSessionPort) -> None:
        self._repo = repo
        self._db = db_session

    def execute(self, *, project_id: UUID, quote_id: UUID) -> ChiffrageQuote:
        quote = owned_quote(self._repo, quote_id, project_id)

        if self._repo.find_article_for_update(quote.article_id) is None:
            raise ArticleNotFoundError(f"Article {quote.article_id} not found.")

        self._repo.clear_selection(quote.article_id)
        selected = quote.with_selection(True)
        self._repo.save_quote(selected)
        self._db.commit()
        return selected
