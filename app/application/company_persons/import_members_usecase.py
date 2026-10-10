"""ImportMembersUseCase — copy people from another company the admin also manages.

Copies PROFILES only (no pay data: `labor_role_id` / `default_daily_rate` stay
unset on the new row — the target company sets its own rate) and attaches any
already-linked user account as a `member` of the target company.
"""

from __future__ import annotations

import dataclasses
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import uuid4

from app.application.companies._helpers import _assert_company_admin
from app.application.companies.ports import (
    RoleCheckerPort,
    TransactionalSessionPort,
    UserCompanyAccessRepositoryPort,
)
from app.application.company_persons.add_member_by_phone_usecase import _PENDING_WINDOW_DAYS
from app.application.company_persons.dtos import ImportedMember, ImportMembersInput, ImportMembersResult
from app.application.company_persons.exceptions import SourceCompanyNotAccessibleError
from app.application.company_persons.ports import CompanyPersonRepositoryPort
from app.application.persons.ports import IPersonRepository
from app.domain.companies.roles import CompanyRole
from app.domain.companies.user_company_access import UserCompanyAccess
from app.domain.entities.company_person import CompanyPerson


class ImportMembersUseCase:
    def __init__(
        self,
        access_repo: UserCompanyAccessRepositoryPort,
        person_repo: IPersonRepository,
        company_person_repo: CompanyPersonRepositoryPort,
        role_checker: RoleCheckerPort,
    ) -> None:
        self._access = access_repo
        self._persons = person_repo
        self._company_persons = company_person_repo
        self._role_checker = role_checker

    def execute(self, inp: ImportMembersInput, db_session: TransactionalSessionPort) -> ImportMembersResult:
        # Caller must be admin of BOTH the source and target company.
        _assert_company_admin(self._role_checker, inp.caller_id, inp.company_id)
        if not (
            self._role_checker.is_platform_admin(inp.caller_id)
            or self._role_checker.is_company_admin(inp.caller_id, inp.from_company_id)
        ):
            raise SourceCompanyNotAccessibleError(inp.from_company_id)

        now = datetime.now(timezone.utc)
        items: list[ImportedMember] = []
        skipped_person_ids: list = []
        already_member_person_ids: list = []

        for person_id in inp.person_ids:
            source_profile = self._company_persons.find(inp.from_company_id, person_id)
            if source_profile is None:
                # Not actually a member of the source company — skip rather
                # than failing the whole batch on one bad id, but report it
                # back so the caller can tell "imported" from "silently lost".
                skipped_person_ids.append(person_id)
                continue
            person = self._persons.find_by_id(person_id)
            if person is None:
                skipped_person_ids.append(person_id)
                continue

            # Someone without an account only joins on sign-up through a pending
            # profile (`list_pending_by_phone`), so their copy here is pending too,
            # as a member (the import role; "member" is stored as None).
            pending_expires_at: Optional[datetime] = None
            if person.user_id is None:
                source_until = source_profile.pending_expires_at
                pending_expires_at = (
                    source_until
                    if source_until is not None and source_until > now
                    else now + timedelta(days=_PENDING_WINDOW_DAYS)
                )

            target_profile = self._company_persons.find(inp.company_id, person_id)
            added = target_profile is None
            if target_profile is None:
                target_profile = self._company_persons.save(
                    CompanyPerson(
                        id=uuid4(),
                        company_id=inp.company_id,
                        person_id=person_id,
                        created_at=now,
                        is_active=True,
                        phone_normalized=source_profile.phone_normalized,
                        pending_expires_at=pending_expires_at,
                        created_by_user_id=inp.caller_id,
                        # No pay data copied: labor_role_id / default_daily_rate
                        # left unset — the destination company sets its own rate.
                    )
                )
            elif not target_profile.is_active:
                # Booted earlier: bring the profile back, or the re-attached user
                # stays hidden from the directory and the assign-member pickers.
                target_profile = self._company_persons.save(
                    dataclasses.replace(
                        target_profile, is_active=True, pending_expires_at=pending_expires_at, pending_company_role=None
                    )
                )
                added = True

            if person.user_id is not None and self._access.find(person.user_id, inp.company_id) is None:
                self._access.save(
                    UserCompanyAccess(
                        user_id=person.user_id,
                        company_id=inp.company_id,
                        is_primary=len(self._access.list_for_user(person.user_id)) == 0,
                        attached_at=now,
                        role=CompanyRole.MEMBER.value,
                    )
                )
                added = True

            if not added:
                # Already in the target company's directory (and attached, when linked):
                # nothing was imported, so it is not counted as imported.
                already_member_person_ids.append(person_id)
                continue

            items.append(
                ImportedMember(
                    person_id=person.id,
                    name=person.name,
                    phone=person.phone,
                    linked_user_id=person.user_id,
                )
            )

        db_session.commit()
        return ImportMembersResult(
            items=items,
            skipped_person_ids=skipped_person_ids,
            already_member_person_ids=already_member_person_ids,
        )
