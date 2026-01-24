"""List projects use case."""

from dataclasses import dataclass
from typing import List, Optional
from uuid import UUID

from app.application.projects.ports import IProjectRepository


@dataclass
class ProjectSummary:
    id: str
    name: str
    address: Optional[str]
    owner_id: str
    user_count: int


class ListProjectsUseCase:
    """List projects for a user or all (admin)."""

    def __init__(self, project_repo: IProjectRepository):
        self._repo = project_repo

    def execute(self, user_id: UUID, is_admin: bool = False) -> List[ProjectSummary]:
        if is_admin:
            projects = self._repo.list_all()
        else:
            projects = self._repo.list_by_user(user_id)

        return [
            ProjectSummary(
                id=str(p.id),
                name=p.name,
                address=p.address,
                owner_id=str(p.owner_id),
                user_count=len(p.user_ids) if p.user_ids else 0,
            )
            for p in projects
        ]
