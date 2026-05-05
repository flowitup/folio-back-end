"""Repository and session ports (Protocols) for the billing application layer.

All protocols are structural (no runtime_checkable) — type-checked only.
Infrastructure implementations live in app/infrastructure/billing/.
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from typing import Any, Optional, Protocol
from uuid import UUID

from app.domain.billing.company_profile import CompanyProfile
from app.domain.billing.document import BillingDocument
from app.domain.billing.enums import BillingDocumentKind, BillingDocumentStatus
from app.domain.billing.template import BillingDocumentTemplate


class BillingDocumentRepositoryPort(Protocol):
    """Persistence contract for BillingDocument aggregates."""

    def find_by_id(self, doc_id: UUID) -> Optional[BillingDocument]:
        """Return document by UUID, or None if not found."""
        ...

    def find_by_id_for_update(self, doc_id: UUID) -> Optional[BillingDocument]:
        """Return document by UUID with SELECT FOR UPDATE lock, or None.

        Serializes concurrent operations against the same document row.
        SQLite test implementations may degrade to a plain SELECT.
        """
        ...

    def list_for_user(
        self,
        user_id: UUID,
        kind: BillingDocumentKind,
        status: Optional[BillingDocumentStatus] = None,
        project_id: Optional[UUID] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[BillingDocument], int]:
        """Return paginated documents for a user, with total count.

        Filters: kind required; status and project_id are optional.
        Returns (items, total_count) where total_count is the unfiltered total
        matching user_id + kind + optional filters (for pagination metadata).
        """
        ...

    def save(self, doc: BillingDocument) -> BillingDocument:
        """Insert or update a document. Returns the persisted instance."""
        ...

    def delete(self, doc_id: UUID) -> None:
        """Hard-delete a document by UUID."""
        ...

    def find_by_source_devis_id(self, devis_id: UUID) -> Optional[BillingDocument]:
        """Return the facture row linked to a given source devis, or None.

        Used by ConvertDevisToFactureUseCase as a race-condition guard —
        ensures a devis can only be converted once.
        """
        ...


class BillingTemplateRepositoryPort(Protocol):
    """Persistence contract for BillingDocumentTemplate aggregates."""

    def find_by_id(self, template_id: UUID) -> Optional[BillingDocumentTemplate]:
        """Return template by UUID, or None if not found."""
        ...

    def list_for_user(
        self,
        user_id: UUID,
        kind: Optional[BillingDocumentKind] = None,
    ) -> list[BillingDocumentTemplate]:
        """Return all templates for a user, optionally filtered by kind."""
        ...

    def save(self, template: BillingDocumentTemplate) -> BillingDocumentTemplate:
        """Insert or update a template. Returns the persisted instance."""
        ...

    def delete(self, template_id: UUID) -> None:
        """Hard-delete a template by UUID."""
        ...


class CompanyProfileRepositoryPort(Protocol):
    """Persistence contract for CompanyProfile (one row per user)."""

    def find_by_user_id(self, user_id: UUID) -> Optional[CompanyProfile]:
        """Return the company profile for a user, or None if not configured."""
        ...

    def save(self, profile: CompanyProfile) -> CompanyProfile:
        """Insert or upsert a company profile. Returns the persisted instance."""
        ...


class BillingNumberCounterRepositoryPort(Protocol):
    """Atomic counter port for generating sequential document numbers.

    Implementations MUST use SELECT ... FOR UPDATE on the counter row and
    increment it within the same transaction to guarantee uniqueness under
    concurrent creates.
    """

    def next_value(self, user_id: UUID, kind: BillingDocumentKind, year: int) -> int:
        """Return the next sequence value (1-based, monotonically increasing).

        Atomically increments the counter for (user_id, kind, year).
        Creates the counter row with value=1 if it does not yet exist.
        """
        ...


class BillingDocumentPdfRendererPort(Protocol):
    """PDF rendering contract for billing documents."""

    def render(self, doc: BillingDocument) -> bytes:
        """Render a billing document to a PDF byte string."""
        ...


class TransactionalSessionPort(Protocol):
    """Minimal session contract shared by all mutating billing use-cases.

    Matches the contract defined in app.application.invitations.ports.
    Production code passes db.session; tests pass a stub.
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
