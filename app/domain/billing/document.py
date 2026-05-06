"""BillingDocument domain entity for the billing bounded context.

Polymorphic immutable dataclass with kind=devis|facture discriminator.
Mirrors the DB schema columns exactly (issuer snapshot fields included).
Computed totals are properties — never stored, always recomputed from items.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Mapping, Optional
from uuid import UUID

from app.domain.billing.enums import BillingDocumentKind, BillingDocumentStatus
from app.domain.billing.totals import compute_totals, vat_breakdown
from app.domain.billing.value_objects import BillingDocumentItem


@dataclass(frozen=True, slots=True)
class BillingDocument:
    """Immutable billing document entity (devis or facture).

    All fields map 1-to-1 to billing_documents DB columns.
    items is stored as a tuple to preserve immutability.

    Kind-specific nullable fields:
      validity_until      — devis only (None on facture)
      payment_due_date    — facture only (None on devis)
      payment_terms       — facture only (None on devis)

    Issuer snapshot fields (issuer_*) are copied from CompanyProfile at create
    time and never mutated by subsequent CompanyProfile edits.
    """

    # --- identity ---
    id: UUID
    user_id: UUID
    kind: BillingDocumentKind
    document_number: str
    status: BillingDocumentStatus

    # --- dates ---
    issue_date: date
    created_at: datetime
    updated_at: datetime

    # --- recipient (freetext v1) ---
    recipient_name: str

    # --- issuer snapshot (copied from CompanyProfile at create time) ---
    issuer_legal_name: str
    issuer_address: str

    # --- line items ---
    items: tuple[BillingDocumentItem, ...] = field(default_factory=tuple)

    # --- issuing company (FK to companies; nullable for legacy documents) ---
    company_id: Optional[UUID] = None

    # --- optional project link ---
    project_id: Optional[UUID] = None

    # --- kind-specific dates ---
    validity_until: Optional[date] = None  # devis only
    payment_due_date: Optional[date] = None  # facture only
    payment_terms: Optional[str] = None  # facture only

    # --- recipient (optional) ---
    recipient_address: Optional[str] = None
    recipient_email: Optional[str] = None
    recipient_siret: Optional[str] = None

    # --- document body ---
    notes: Optional[str] = None
    terms: Optional[str] = None
    signature_block_text: Optional[str] = None

    # --- issuer snapshot (optional) ---
    issuer_siret: Optional[str] = None
    issuer_tva_number: Optional[str] = None
    issuer_iban: Optional[str] = None
    issuer_bic: Optional[str] = None
    issuer_logo_url: Optional[str] = None

    # --- devis → facture conversion audit trail ---
    source_devis_id: Optional[UUID] = None

    # ------------------------------------------------------------------
    # Computed totals — derived from items, never stored
    # ------------------------------------------------------------------

    @property
    def total_ht(self) -> Decimal:
        """Sum of all line totals before VAT."""
        return compute_totals(self.items).total_ht

    @property
    def total_tva_by_rate(self) -> Mapping[Decimal, Decimal]:
        """VAT amounts grouped by normalized rate, e.g. {Decimal("20"): Decimal("40")}."""
        return compute_totals(self.items).total_tva_by_rate

    @property
    def total_tva(self) -> Decimal:
        """Total VAT across all rates."""
        return compute_totals(self.items).total_tva

    @property
    def total_ttc(self) -> Decimal:
        """Grand total including all VAT."""
        return compute_totals(self.items).total_ttc

    @property
    def vat_breakdown(self) -> list[tuple[Decimal, Decimal, Decimal]]:
        """List of (rate, base_ht, tva_amount) tuples sorted by rate descending."""
        return vat_breakdown(self.items)

    # ------------------------------------------------------------------
    # Mutation helper
    # ------------------------------------------------------------------

    def with_updates(self, **kwargs: object) -> "BillingDocument":
        """Return a new BillingDocument with the given fields replaced.

        All other fields are carried over unchanged (frozen dataclass semantics).
        """
        return dataclasses.replace(self, **kwargs)

    # ------------------------------------------------------------------
    # Equality + hashing by identity
    # ------------------------------------------------------------------

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, BillingDocument):
            return NotImplemented
        return self.id == other.id

    def __hash__(self) -> int:
        return hash(self.id)
