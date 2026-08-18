"""ChiffrageQuote domain entity — one supplier price offer for an article."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional
from uuid import UUID, uuid4

_HUNDRED = Decimal("100")


@dataclass(frozen=True)
class ChiffrageQuote:
    """A price from one fournisseur, used to compare offers for the same article.

    Prices are stored canonically **HT** with the VAT rate alongside, because
    French suppliers publish inconsistently (Leroy Merlin shelf prices are TTC,
    Point P quotes are HT) and comparing raw numbers across the two is a 20%
    error. TTC is always derived, never stored.

    A quote identifies its fournisseur either by ``supplier_id`` (a bibliothèque
    supplier) or by free-text ``supplier_name``; at least one is required. The
    free-text name also survives as a readable snapshot if the linked supplier
    row is later deleted.
    """

    id: UUID
    article_id: UUID
    supplier_id: Optional[UUID]
    supplier_name: Optional[str]
    library_product_id: Optional[UUID]
    unit_price_ht: Decimal
    tva_rate: Decimal
    product_url: Optional[str]
    note: Optional[str]
    is_selected: bool
    created_at: datetime
    updated_at: datetime

    # Sentinel distinguishing "leave unchanged" (omitted) from an explicit clear.
    _UNSET = object()

    @property
    def unit_price_ttc(self) -> Decimal:
        """Unit price including VAT, at full precision — callers quantize."""
        return self.unit_price_ht * (Decimal("1") + self.tva_rate / _HUNDRED)

    @classmethod
    def create(
        cls,
        *,
        article_id: UUID,
        unit_price_ht: Decimal,
        tva_rate: Decimal,
        supplier_id: Optional[UUID] = None,
        supplier_name: Optional[str] = None,
        library_product_id: Optional[UUID] = None,
        product_url: Optional[str] = None,
        note: Optional[str] = None,
    ) -> "ChiffrageQuote":
        """Create a new, unselected quote."""
        now = datetime.now(timezone.utc)
        return cls(
            id=uuid4(),
            article_id=article_id,
            supplier_id=supplier_id,
            supplier_name=supplier_name,
            library_product_id=library_product_id,
            unit_price_ht=unit_price_ht,
            tva_rate=tva_rate,
            product_url=product_url,
            note=note,
            is_selected=False,
            created_at=now,
            updated_at=now,
        )

    def with_updates(
        self,
        *,
        supplier_id: object = _UNSET,
        supplier_name: object = _UNSET,
        library_product_id: object = _UNSET,
        unit_price_ht: object = _UNSET,
        tva_rate: object = _UNSET,
        product_url: object = _UNSET,
        note: object = _UNSET,
    ) -> "ChiffrageQuote":
        """Return a copy with the given editable fields overwritten."""
        U = ChiffrageQuote._UNSET
        return replace(
            self,
            supplier_id=self.supplier_id if supplier_id is U else supplier_id,
            supplier_name=self.supplier_name if supplier_name is U else supplier_name,
            library_product_id=(self.library_product_id if library_product_id is U else library_product_id),
            unit_price_ht=self.unit_price_ht if unit_price_ht is U else unit_price_ht,
            tva_rate=self.tva_rate if tva_rate is U else tva_rate,
            product_url=self.product_url if product_url is U else product_url,
            note=self.note if note is U else note,
            updated_at=datetime.now(timezone.utc),
        )

    def with_selection(self, is_selected: bool) -> "ChiffrageQuote":
        """Return a copy flagged (or unflagged) as the retained quote."""
        return replace(self, is_selected=is_selected, updated_at=datetime.now(timezone.utc))
