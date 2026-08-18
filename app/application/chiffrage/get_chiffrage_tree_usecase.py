"""GetChiffrageTreeUseCase — read the whole costing tree of a project."""

from __future__ import annotations

from uuid import UUID

from app.application.chiffrage.dtos import ChiffrageTreeResponse, build_tree_response
from app.application.chiffrage.ports import ChiffrageRepositoryPort


class GetChiffrageTreeUseCase:
    """Return postes -> articles -> quotes with totals, in one call.

    Authorization lives entirely in the route decorators (require_permission +
    require_project_access), the same pair that guards the write endpoints.
    Checking membership here as well would use a different source than the
    writes do, which is how a caller ends up able to modify a chiffrage they
    cannot read.
    """

    def __init__(self, repo: ChiffrageRepositoryPort) -> None:
        self._repo = repo

    def execute(self, *, project_id: UUID) -> ChiffrageTreeResponse:
        """Assemble the project's chiffrage tree."""
        postes, articles_by_poste, quotes_by_article = self._repo.get_tree(project_id)
        return build_tree_response(project_id, postes, articles_by_poste, quotes_by_article)
