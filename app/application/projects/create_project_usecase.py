"""Create project use case."""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID, uuid4

from app.application.projects.ports import IProjectRepository
from app.domain.entities.project import Project
from app.domain.exceptions.project_exceptions import InvalidProjectDataError


@dataclass
class CreateProjectRequest:
    name: str
    owner_id: UUID
    address: Optional[str] = None


@dataclass
class CreateProjectResponse:
    id: str
    name: str
    address: Optional[str]
    owner_id: str
    created_at: str


class CreateProjectUseCase:
    """Create a new construction project."""

    def __init__(self, project_repo: IProjectRepository):
        self._repo = project_repo

    def execute(self, request: CreateProjectRequest) -> CreateProjectResponse:
        if not request.name or len(request.name.strip()) == 0:
            raise InvalidProjectDataError("Project name is required")
        if len(request.name) > 255:
            raise InvalidProjectDataError("Project name exceeds 255 characters")

        project = Project(
            id=uuid4(),
            name=request.name.strip(),
            address=request.address.strip() if request.address else None,
            owner_id=request.owner_id,
            created_at=datetime.now(timezone.utc),
        )

        saved = self._repo.create(project)

        return CreateProjectResponse(
            id=str(saved.id),
            name=saved.name,
            address=saved.address,
            owner_id=str(saved.owner_id),
            created_at=saved.created_at.isoformat(),
        )
