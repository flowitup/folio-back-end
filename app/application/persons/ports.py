"""Person repository port."""

from abc import ABC, abstractmethod
from typing import List, Optional
from uuid import UUID

from app.domain.entities.person import Person


class IPersonRepository(ABC):
    """Port for Person persistence operations."""

    @abstractmethod
    def create(self, person: Person) -> Person:
        """Persist a new Person. Returns the created entity."""
        ...

    @abstractmethod
    def find_by_id(self, person_id: UUID) -> Optional[Person]:
        """Find a Person by ID. Returns None if not found."""
        ...

    @abstractmethod
    def search(
        self,
        query: str,
        limit: int = 20,
    ) -> List[Person]:
        """Search persons by name (case-insensitive substring on normalized_name)
        or by exact phone match. Returns up to `limit` rows ordered by name.

        Phase 1b-ii intentionally returns a flat unscoped list. Cross-project
        privacy scoping (limit to persons visible via accessible projects)
        will be layered in Phase 1d once the FE typeahead consumes this.
        """
        ...

    @abstractmethod
    def find_by_phone(self, phone: str) -> Optional[Person]:
        """Find a Person by exact phone match. Returns None if not found.

        Used by Phase 1c backfill to detect identity-equivalence between
        per-project Worker rows.
        """
        ...
