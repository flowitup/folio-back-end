"""ListNewMembersUseCase — derived `company_events[]` feed for GET /notifications.

For every company the caller admins, surfaces members whose `company_persons`
profile was attached in the last 7 days AND who have no `user_projects`
assignment on any of that company's projects yet — a dead end an admin
should notice (finding 12: no notification store; this list is always
computed fresh, never persisted).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, List
from uuid import UUID

from app.application.companies.dtos import NewMemberEvent
from app.application.companies.ports import UserCompanyAccessRepositoryPort
from app.application.company_persons.ports import CompanyPersonRepositoryPort
from app.application.persons.ports import IPersonRepository
from app.domain.companies.roles import CompanyRole

if TYPE_CHECKING:
    from app.application.authz.ports import AuthzReaderPort

_WINDOW_DAYS = 7


class ListNewMembersUseCase:
    def __init__(
        self,
        access_repo: UserCompanyAccessRepositoryPort,
        company_person_repo: CompanyPersonRepositoryPort,
        person_repo: IPersonRepository,
        authz_reader: "AuthzReaderPort",
    ) -> None:
        self._access = access_repo
        self._company_persons = company_person_repo
        self._persons = person_repo
        self._authz = authz_reader

    def execute(self, admin_user_id: UUID) -> List[NewMemberEvent]:
        admin_company_ids = [
            a.company_id for a in self._access.list_for_user(admin_user_id) if a.role == CompanyRole.ADMIN.value
        ]
        if not admin_company_ids:
            return []

        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(days=_WINDOW_DAYS)
        events: List[NewMemberEvent] = []

        for company_id in admin_company_ids:
            recent_profiles = [
                profile
                for profile in self._company_persons.list_for_company(company_id)
                if profile.created_at >= cutoff
            ]
            if not recent_profiles:
                continue

            # H5: batch the two per-profile lookups this loop used to make one
            # at a time — one `find_by_ids` for every recent Person, one
            # `IN`-based query for their assigned project ids in THIS company,
            # instead of 2N queries per company.
            persons_by_id = {
                person.id: person for person in self._persons.find_by_ids([p.person_id for p in recent_profiles])
            }
            linked_user_ids = [p.user_id for p in persons_by_id.values() if p.user_id is not None]
            assigned_by_user = self._authz.assigned_project_ids_for_users(company_id, linked_user_ids)

            for profile in recent_profiles:
                person = persons_by_id.get(profile.person_id)
                if person is None or person.user_id is None:
                    continue
                if assigned_by_user.get(person.user_id):
                    continue
                events.append(
                    NewMemberEvent(
                        user_id=person.user_id,
                        display_name=person.name,
                        company_id=company_id,
                        attached_at=profile.created_at,
                    )
                )
        return events
