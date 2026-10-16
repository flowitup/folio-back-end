"""Project domain entity."""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import List, Optional
from uuid import UUID


@dataclass(slots=True)
class Project:
    """
    Project aggregate root.

    Represents a construction project that users can be assigned to.
    """

    id: UUID
    name: str
    owner_id: UUID
    created_at: datetime
    address: Optional[str] = None
    invoice_prefix: Optional[str] = None
    updated_at: Optional[datetime] = None
    user_ids: List[UUID] = field(default_factory=list)
    # Budget tracking — nullable; None means "no budget set".
    budget: Optional[Decimal] = None
    # Free-text source / funding description for the budget (e.g. "Client contract").
    budget_source: Optional[str] = None
    # Owning company; its admins can read every project of the company without an assignment.
    company_id: Optional[UUID] = None

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Project):
            return NotImplemented
        return self.id == other.id

    def __hash__(self) -> int:
        return hash(self.id)


def project_display_label(project: Project) -> str:
    """The label a project is shown under: its name first, its site address when the name is blank.

    Mirrors the clients' `projectDisplayName` (name first, address second), so someone invited
    to a project reads the same label in the invitation, the bell, chat and documents as in the
    app once they have joined. A project created without a name is named after its address.
    """
    name = project.name.strip() if isinstance(project.name, str) else ""
    if name:
        return name
    return project.address.strip() if isinstance(project.address, str) else ""
