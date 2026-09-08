"""ListDirectoryUseCase — GET /companies/<id>/persons (admin or manager).

Returns every active `company_persons` profile for a company, with
`assigned_project_ids` scoped strictly to THIS company's projects — a person
who also works for another company never leaks that company's project ids
here.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING
from uuid import UUID

from app.application.company_persons.dtos import DirectoryEntry, ListDirectoryResult
from app.application.company_persons.ports import CompanyPersonRepositoryPort
from app.application.persons.ports import IPersonRepository

if TYPE_CHECKING:
    from app.application.authz.ports import AuthzReaderPort


class ListDirectoryUseCase:
    def __init__(
        self,
        company_person_repo: CompanyPersonRepositoryPort,
        person_repo: IPersonRepository,
        authz_reader: "AuthzReaderPort",
    ) -> None:
        self._company_persons = company_person_repo
        self._persons = person_repo
        self._authz = authz_reader

    def execute(self, company_id: UUID) -> ListDirectoryResult:
        profiles = self._company_persons.list_for_company(company_id, include_inactive=True)
        company_project_ids = self._authz.project_ids_for_company(company_id)
        now = datetime.now(timezone.utc)

        items: list[DirectoryEntry] = []
        for profile in profiles:
            person = self._persons.find_by_id(profile.person_id)
            if person is None:
                continue
            assigned = (
                self._authz.assigned_project_ids(person.user_id, company_project_ids)
                if person.user_id is not None
                else []
            )
            items.append(
                DirectoryEntry(
                    person_id=person.id,
                    name=person.name,
                    phone=person.phone,
                    linked_user_id=person.user_id,
                    assigned_project_ids=assigned,
                    is_active=profile.is_active,
                    pending=profile.is_pending(now),
                    labor_role_id=profile.labor_role_id,
                    default_daily_rate=profile.default_daily_rate,
                )
            )
        return ListDirectoryResult(items=items)
