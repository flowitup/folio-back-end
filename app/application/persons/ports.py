"""Person repository port."""

from abc import ABC, abstractmethod
from typing import List, Optional
from uuid import UUID

from app.domain.entities.person import Person


class IPersonRepository(ABC):
    """Port for Person persistence operations."""

    @abstractmethod
    def create(self, person: Person, *, commit: bool = True) -> Person:
        """Persist a new Person. Returns the created entity.

        `commit=False` (M8) flushes only — used by multi-write onboarding use
        cases that must commit every write together at the end of their own
        transaction, so a Person insert never survives as a committed orphan
        if a later step in the same use case fails.
        """
        ...

    @abstractmethod
    def find_by_id(self, person_id: UUID) -> Optional[Person]:
        """Find a Person by ID. Returns None if not found."""
        ...

    @abstractmethod
    def find_by_ids(self, person_ids: List[UUID]) -> List[Person]:
        """Return every Person whose id is in `person_ids` (H5 batch lookup).

        Order is unspecified — callers that need a stable order re-key by id.
        An empty `person_ids` returns an empty list without querying.
        """
        ...

    @abstractmethod
    def find_by_user_id(self, user_id: UUID) -> Optional[Person]:
        """Find the Person linked to a user account, or None (Phase 2).

        A user has at most one Person (``persons.user_id`` is unique) —
        set once, either by the backfill migration or by
        ``set_user_id`` on sign-up linking.
        """
        ...

    @abstractmethod
    def set_user_id(self, person_id: UUID, user_id: UUID, *, commit: bool = True) -> Optional[Person]:
        """Link a Person to a user account (Phase 2 sign-up linking).

        Returns the updated Person, or None if `person_id` does not exist.
        Does not check for a pre-existing link on `user_id` — the caller
        (sign-up linking use case) owns that invariant since it must decide
        what to do about a conflict, not silently overwrite. `commit=False`
        (M8) flushes only — see `create`.
        """
        ...

    @abstractmethod
    def search(
        self,
        query: str,
        limit: int = 20,
        company_ids: Optional[List[UUID]] = None,
    ) -> List[Person]:
        """Search persons by name (case-insensitive substring on normalized_name)
        or by exact phone match. Returns up to `limit` rows ordered by name.

        `company_ids` (Phase 2): when given, restricts results to persons
        with an active `company_persons` row in one of those companies —
        the tenancy scope for `GET /persons` (see
        `app.application.persons.search_persons.SearchPersonsUseCase`).
        `None` means unscoped (platform `*:*` callers only); an empty list
        would incorrectly behave like "no filter" in a plain SQL `IN ()`, so
        callers MUST resolve at least one company id before scoping, never
        pass `[]` expecting "no results" — the use case enforces this by
        returning early with an empty response instead of calling `search`.
        """
        ...

    @abstractmethod
    def has_references(self, person_id: UUID) -> bool:
        """Return True if any Worker row still references `person_id`.

        Used before deleting an orphaned duplicate Person (C1 sign-up
        merge): `workers.person_id` is `ON DELETE RESTRICT`, so a naive
        `delete()` would raise on SQLite too late (or not at all — SQLite's
        test engine does not enforce FK constraints) to distinguish "safe
        to delete" from "must stay, unlinked, for ops review".
        """
        ...

    @abstractmethod
    def delete(self, person_id: UUID) -> bool:
        """Hard-delete a Person row. Returns True if a row was deleted.

        The caller is responsible for ensuring no Worker rows reference
        this Person — the DB FK is ON DELETE RESTRICT and will raise an
        IntegrityError otherwise. The merge use case reassigns workers
        first, then calls this.
        """
        ...
