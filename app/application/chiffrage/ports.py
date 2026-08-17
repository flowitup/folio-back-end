"""Repository and session ports (Protocols) for the chiffrage application layer."""

from __future__ import annotations

from typing import Optional, Protocol
from uuid import UUID

from app.domain.entities.chiffrage_article import ChiffrageArticle
from app.domain.entities.chiffrage_poste import ChiffragePoste
from app.domain.entities.chiffrage_quote import ChiffrageQuote
from app.domain.entities.chiffrage_unit import ChiffrageUnit


class ChiffrageRepositoryPort(Protocol):
    """Persistence contract for the chiffrage aggregate (poste / article / quote)."""

    # -- tree read ---------------------------------------------------------

    def get_tree(
        self, project_id: UUID
    ) -> tuple[list[ChiffragePoste], dict[UUID, list[ChiffrageArticle]], dict[UUID, list[ChiffrageQuote]]]:
        """Return the whole chiffrage of a project in a bounded number of queries.

        Returns (postes ordered by position, articles keyed by poste id ordered
        by position, quotes keyed by article id). Implementations must NOT issue
        one query per article — the page renders the full tree in a single call.
        """
        ...

    # -- poste -------------------------------------------------------------

    def find_poste(self, poste_id: UUID) -> Optional[ChiffragePoste]:
        """Return a poste by id, or None."""
        ...

    def add_poste(self, poste: ChiffragePoste) -> None:
        """Insert a new poste."""
        ...

    def save_poste(self, poste: ChiffragePoste) -> None:
        """Update an existing poste."""
        ...

    def delete_poste(self, poste_id: UUID) -> None:
        """Delete a poste; articles and quotes cascade."""
        ...

    def max_poste_position(self, project_id: UUID) -> int:
        """Return the highest poste position in the project, or 0 when empty."""
        ...

    # -- article -----------------------------------------------------------

    def find_article(self, article_id: UUID) -> Optional[ChiffrageArticle]:
        """Return an article by id, or None."""
        ...

    def find_article_for_update(self, article_id: UUID) -> Optional[ChiffrageArticle]:
        """Return an article with a row-level SELECT FOR UPDATE lock, or None.

        Serializes concurrent quote-selection against the same article. SQLite
        (tests) degrades to a plain SELECT — the in-memory DB has no concurrency.
        """
        ...

    def add_article(self, article: ChiffrageArticle) -> None:
        """Insert a new article."""
        ...

    def save_article(self, article: ChiffrageArticle) -> None:
        """Update an existing article."""
        ...

    def delete_article(self, article_id: UUID) -> None:
        """Delete an article; its quotes cascade."""
        ...

    def max_article_position(self, poste_id: UUID) -> int:
        """Return the highest article position within the poste, or 0 when empty."""
        ...

    # -- quote -------------------------------------------------------------

    def find_quote(self, quote_id: UUID) -> Optional[ChiffrageQuote]:
        """Return a quote by id, or None."""
        ...

    def add_quote(self, quote: ChiffrageQuote) -> None:
        """Insert a new quote."""
        ...

    def save_quote(self, quote: ChiffrageQuote) -> None:
        """Update an existing quote."""
        ...

    def delete_quote(self, quote_id: UUID) -> None:
        """Delete a quote."""
        ...

    def clear_selection(self, article_id: UUID) -> None:
        """Unselect every quote of the article (bulk UPDATE, no session sync)."""
        ...

    # -- units -------------------------------------------------------------

    def list_units(self, project_id: UUID) -> list[ChiffrageUnit]:
        """Return the project's custom units ordered by symbol."""
        ...

    def find_unit(self, unit_id: UUID) -> Optional[ChiffrageUnit]:
        """Return a custom unit by id, or None."""
        ...

    def unit_exists(self, project_id: UUID, symbol: str) -> bool:
        """Return True if the project already has that custom unit symbol."""
        ...

    def add_unit(self, unit: ChiffrageUnit) -> None:
        """Insert a new custom unit."""
        ...

    def delete_unit(self, unit_id: UUID) -> None:
        """Delete a custom unit. Articles keep their snapshot symbol."""
        ...

    # -- ownership resolution ---------------------------------------------

    def project_id_for_poste(self, poste_id: UUID) -> Optional[UUID]:
        """Return the owning project id of a poste, or None if it does not exist."""
        ...

    def project_id_for_article(self, article_id: UUID) -> Optional[UUID]:
        """Return the owning project id of an article, walking poste -> project."""
        ...

    def project_id_for_quote(self, quote_id: UUID) -> Optional[UUID]:
        """Return the owning project id of a quote, walking article -> poste -> project."""
        ...


class ProjectOwnerReaderPort(Protocol):
    """Read-only access to a project's owner, for the owner-or-permission check."""

    def owner_id(self, project_id: UUID) -> Optional[UUID]:
        """Return the project's owner id, or None if the project does not exist."""
        ...


# Re-export the shared ports rather than redefining them: the membership contract
# is identical to notes', and the session contract identical to invitations'.
from app.application.notes.ports import ProjectMembershipReaderPort as ProjectMembershipReaderPort  # noqa: E402,F401
from app.application.invitations.ports import TransactionalSessionPort as TransactionalSessionPort  # noqa: E402,F401
