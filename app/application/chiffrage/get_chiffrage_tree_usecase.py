"""GetChiffrageTreeUseCase — read the whole costing tree of a project."""

from __future__ import annotations

from uuid import UUID

from app.application.chiffrage.dtos import ChiffrageTreeResponse, build_tree_response
from app.application.chiffrage.exceptions import NotProjectMemberError
from app.application.chiffrage.ports import ChiffrageRepositoryPort, ProjectMembershipReaderPort


class GetChiffrageTreeUseCase:
    """Return postes -> articles -> quotes with totals, in one call.

    Authorization: the acting user must be a member of the project.
    """

    def __init__(
        self,
        repo: ChiffrageRepositoryPort,
        membership_reader: ProjectMembershipReaderPort,
    ) -> None:
        self._repo = repo
        self._membership = membership_reader

    def execute(self, *, actor_id: UUID, project_id: UUID) -> ChiffrageTreeResponse:
        """Assemble the project's chiffrage tree.

        Raises:
            NotProjectMemberError: actor is not a member of the project.
        """
        if not self._membership.is_member(actor_id, project_id):
            raise NotProjectMemberError(f"User {actor_id} is not a member of project {project_id}.")

        postes, articles_by_poste, quotes_by_article = self._repo.get_tree(project_id)
        return build_tree_response(project_id, postes, articles_by_poste, quotes_by_article)
