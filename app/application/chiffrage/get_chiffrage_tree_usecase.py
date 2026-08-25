"""GetChiffrageTreeUseCase — read the whole costing tree of a project."""

from __future__ import annotations

from uuid import UUID

from app.application.chiffrage.dtos import ChiffrageTreeResponse, build_tree_response
from app.application.chiffrage.ports import ChiffrageRepositoryPort


class GetChiffrageTreeUseCase:
    """Return postes -> articles -> quotes with totals and shops, in one call.

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
        stores = self._repo.stores_for_project(project_id)

        # One keyed lookup for the whole tree: which linked library products
        # actually have an image an article can borrow as its thumbnail.
        product_ids = [
            q.library_product_id
            for quotes in quotes_by_article.values()
            for q in quotes
            if q.library_product_id is not None
        ]
        library_with_image = self._repo.library_products_with_image(product_ids)

        return build_tree_response(
            project_id,
            postes,
            articles_by_poste,
            quotes_by_article,
            stores,
            library_with_image,
            self._repo.list_rooms(project_id),
        )
