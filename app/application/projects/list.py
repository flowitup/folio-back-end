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
    """List projects visible to the caller.

    Visibility (the `project:create`-as-see-everything proxy removed in
    Phase 1 of the roles/permissions redesign):
      - `is_platform_admin=True` (legacy global `*:*`) → every project.
      - Otherwise → projects the caller owns, is a member of, or that belong
        to one of `admin_company_ids` (companies where the caller holds the
        company "admin" role — implicit admin on every project of their
        own company, never another tenant's).
    """

    def __init__(self, project_repo: IProjectRepository):
        self._repo = project_repo

    def execute(
        self,
        user_id: UUID,
        admin_company_ids: Optional[List[UUID]] = None,
        is_platform_admin: bool = False,
    ) -> List[ProjectSummary]:
        if is_platform_admin:
            projects = self._repo.list_all()
        else:
            projects = self._repo.list_for_user_and_companies(user_id, admin_company_ids or [])

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
