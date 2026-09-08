"""Search Persons use case (typeahead)."""

from dataclasses import dataclass, field
from typing import List, Optional
from uuid import UUID

from app.application.persons.ports import IPersonRepository


@dataclass
class SearchPersonsRequest:
    query: str = ""
    limit: int = 20
    # Phase 2 tenancy scope. `None` = unscoped (platform `*:*` caller — the
    # route never sets this for anyone else). An empty list means the
    # caller has zero admin/manager companies to search within; the use
    # case short-circuits to an empty response rather than querying with an
    # empty `IN ()` (which some backends treat as "no filter").
    company_ids: Optional[List[UUID]] = field(default=None)


@dataclass
class PersonSummary:
    id: str
    name: str
    phone: Optional[str]


@dataclass
class SearchPersonsResponse:
    persons: List[PersonSummary]
    total: int


class SearchPersonsUseCase:
    """Typeahead lookup for the worker-assignment flow.

    Returns up to ``limit`` persons whose normalized_name contains the
    query substring, or whose phone matches exactly. Empty query returns
    the first ``limit`` persons alphabetically — useful for "browse all"
    behavior in an empty search box.
    """

    DEFAULT_LIMIT = 20
    MAX_LIMIT = 100

    def __init__(self, person_repo: IPersonRepository):
        self._repo = person_repo

    def execute(self, request: SearchPersonsRequest) -> SearchPersonsResponse:
        limit = max(1, min(request.limit or self.DEFAULT_LIMIT, self.MAX_LIMIT))
        query = (request.query or "").strip()

        # Scoped caller (not platform *:*) with no admin/manager company at
        # all → nothing to search; avoid an empty-list `IN ()` ambiguity in
        # the repository layer by short-circuiting here instead.
        if request.company_ids is not None and len(request.company_ids) == 0:
            return SearchPersonsResponse(persons=[], total=0)

        rows = self._repo.search(query=query, limit=limit, company_ids=request.company_ids)

        return SearchPersonsResponse(
            persons=[PersonSummary(id=str(p.id), name=p.name, phone=p.phone) for p in rows],
            total=len(rows),
        )
