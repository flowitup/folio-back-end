"""Search Persons use case (typeahead)."""

from dataclasses import dataclass
from typing import List, Optional

from app.application.persons.ports import IPersonRepository


@dataclass
class SearchPersonsRequest:
    query: str = ""
    limit: int = 20


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

        rows = self._repo.search(query=query, limit=limit)

        return SearchPersonsResponse(
            persons=[
                PersonSummary(id=str(p.id), name=p.name, phone=p.phone)
                for p in rows
            ],
            total=len(rows),
        )
