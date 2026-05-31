"""Supplier domain entity — a product supplier scoped to a company."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID, uuid4


@dataclass(frozen=True)
class Supplier:
    """Immutable supplier entity for the bibliotheque bounded context.

    Suppliers are company-scoped; the same physical supplier can be registered
    independently by different companies (no global supplier registry).
    """

    id: UUID
    company_id: UUID
    name: str
    slug: str
    website_url: str | None
    logo_url: str | None
    product_url_template: str | None
    created_at: datetime

    @classmethod
    def create(
        cls,
        *,
        company_id: UUID,
        name: str,
        slug: str,
        website_url: str | None = None,
        logo_url: str | None = None,
        product_url_template: str | None = None,
    ) -> "Supplier":
        """Create a new supplier with generated id and current UTC timestamp."""
        return cls(
            id=uuid4(),
            company_id=company_id,
            name=name,
            slug=slug,
            website_url=website_url,
            logo_url=logo_url,
            product_url_template=product_url_template,
            created_at=datetime.now(timezone.utc),
        )

    @classmethod
    def for_leroy_merlin(cls, company_id: UUID) -> "Supplier":
        """Factory for the Leroy Merlin supplier with canonical defaults."""
        return cls.create(
            company_id=company_id,
            name="Leroy Merlin",
            slug="leroy-merlin",
            website_url="https://www.leroymerlin.fr",
            product_url_template="https://www.leroymerlin.fr/produits/{reference}.html",
        )
