"""Repository and session ports (Protocols) for the billing application layer.

All protocols are structural (no runtime_checkable) — type-checked only.
Infrastructure implementations live in app/infrastructure/billing/.
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from typing import Any, Optional, Protocol
from uuid import UUID

from app.domain.billing.document import BillingDocument
from app.domain.billing.enums import BillingDocumentKind, BillingDocumentStatus
from app.domain.billing.template import BillingDocumentTemplate
from app.domain.billing.exceptions import CompanyNotAttachedError, ForbiddenProjectAccessError
from app.domain.companies.company import Company
from app.domain.companies.user_company_access import UserCompanyAccess


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
        company_id: Optional[UUID] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[BillingDocument], int]:
        """Return paginated documents for a user, with total count.

        Filters: kind required; status, project_id, and company_id are optional.
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


class ProjectReadPort(Protocol):
    """Minimal project read port for billing project:read authorization checks.

    Billing use-cases only need to verify that a user can read a project.
    They do not need full project CRUD operations.
    """

    def find_by_id(self, project_id: UUID) -> Optional[Any]:
        """Return the project entity by UUID, or None if not found."""
        ...


def assert_project_read_access(
    project_repo: Optional[ProjectReadPort],
    project_id: Optional[UUID],
    user_id: UUID,
) -> None:
    """Verify the user has project:read access on *project_id*.

    A user has project:read if they are the project owner or a project member.
    Raises ForbiddenProjectAccessError if access is denied.
    Raises ValueError if the project does not exist.
    No-op when project_id is None or project_repo is None (test / no-project context).
    """
    if project_id is None or project_repo is None:
        return

    project = project_repo.find_by_id(project_id)
    if project is None:
        raise ValueError(f"Project {project_id} not found")

    if project.owner_id == user_id:
        return
    if user_id in (project.user_ids or []):
        return
    raise ForbiddenProjectAccessError(project_id)


class CompanyRepositoryPort(Protocol):
    """Minimal company read port for billing use-cases.

    Only the subset of CompanyRepositoryPort needed to snapshot issuer fields.
    """

    def find_by_id(self, company_id: UUID) -> Optional[Company]:
        """Return company by UUID, or None if not found."""
        ...


class UserCompanyAccessRepositoryPort(Protocol):
    """Minimal user-company access read port for billing attachment checks."""

    def find(self, user_id: UUID, company_id: UUID) -> Optional[UserCompanyAccess]:
        """Return the access row for (user_id, company_id), or None."""
        ...


def assert_user_company_access(
    access_repo: Optional[UserCompanyAccessRepositoryPort],
    company_repo: Optional[CompanyRepositoryPort],
    user_id: UUID,
    company_id: Optional[UUID],
) -> Optional[Company]:
    """Verify the user is attached to company_id and return the full Company snapshot.

    Returns None when company_id is None (no company context; backwards-compatible).
    Raises CompanyNotAttachedError if the user has no access row (race condition guard).
    Raises ValueError if the company does not exist.
    No-op (returns None) when either repo is None (test / legacy context).
    """
    if company_id is None:
        return None
    if access_repo is None or company_repo is None:
        return None

    company = company_repo.find_by_id(company_id)
    if company is None:
        raise ValueError(f"Company {company_id} not found")

    access = access_repo.find(user_id, company_id)
    if access is None:
        raise CompanyNotAttachedError(user_id, company_id)

    return company


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
