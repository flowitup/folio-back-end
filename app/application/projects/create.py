"""Create project use case."""

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional
from uuid import UUID, uuid4

from app.application.projects.ports import IProjectRepository
from app.domain.entities.project import Project
from app.domain.exceptions.project_exceptions import InvalidProjectDataError


@dataclass
class CreateProjectRequest:
    # The site address identifies a project and is mandatory; the name is an
    # optional label that defaults to the address.
    address: str
    owner_id: UUID
    name: Optional[str] = None
    budget: Optional[Decimal] = None
    budget_source: Optional[str] = None
    # Tenant the project belongs to. Resolved by the route (body company_id →
    # caller's primary company → the single company they admin) before this
    # DTO is built; None means the caller could not be tied to any company
    # (legacy `*:*` holder creating without one — allowed, orphaned by design
    # until an admin assigns a company via project settings).
    company_id: Optional[UUID] = None


@dataclass
class CreateProjectResponse:
    id: str
    name: str
    address: Optional[str]
    owner_id: str
    created_at: str
    invoice_prefix: Optional[str] = None
    budget: Optional[Decimal] = None
    budget_source: Optional[str] = None
    company_id: Optional[str] = None


NAME_MAX_LENGTH = 255


def derive_project_name(name: Optional[str], address: str) -> str:
    """Resolve the stored name: the trimmed label when given, else the address.

    The name column stays NOT NULL so every list, invoice and switcher keeps
    working; an address longer than the name column is cut to fit.
    """
    cleaned = (name or "").strip()
    if not cleaned:
        return address_label(address)
    if len(cleaned) > NAME_MAX_LENGTH:
        raise InvalidProjectDataError("Project name exceeds 255 characters")
    return cleaned


def address_label(address: str) -> str:
    """The name a project carries when it is labelled by its address."""
    return address[:NAME_MAX_LENGTH]


def is_address_labelled(project: Project) -> bool:
    """True when the project has no custom label: its name is its address label."""
    return bool(project.address) and project.name == address_label(project.address)


class CreateProjectUseCase:
    """Create a new construction project."""

    def __init__(self, project_repo: IProjectRepository):
        self._repo = project_repo

    def execute(self, request: CreateProjectRequest) -> CreateProjectResponse:
        address = (request.address or "").strip()
        if not address:
            raise InvalidProjectDataError("Project address is required")
        if len(address) > 500:
            raise InvalidProjectDataError("Project address exceeds 500 characters")
        name = derive_project_name(request.name, address)

        project = Project(
            id=uuid4(),
            name=name,
            address=address,
            owner_id=request.owner_id,
            created_at=datetime.now(timezone.utc),
            budget=request.budget,
            budget_source=request.budget_source.strip() if request.budget_source else None,
        )

        saved = self._repo.create(project, company_id=request.company_id)

        return CreateProjectResponse(
            id=str(saved.id),
            name=saved.name,
            address=saved.address,
            owner_id=str(saved.owner_id),
            created_at=saved.created_at.isoformat(),
            # New projects have no invoice prefix until set in project settings.
            invoice_prefix=getattr(saved, "invoice_prefix", None),
            budget=saved.budget,
            budget_source=saved.budget_source,
            company_id=str(request.company_id) if request.company_id else None,
        )
