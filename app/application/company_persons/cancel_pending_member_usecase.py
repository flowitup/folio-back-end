"""CancelPendingMemberUseCase — DELETE /companies/<id>/members/<person_id> (admin).

An admin who adds someone by phone before they have an account leaves a
pending `company_persons` profile behind: whoever signs up with that number
inside the pending window is attached automatically with the role chosen
then (`LinkPersonOnSignupUseCase`). A typo in the number, or a change of
mind, must be undoable, so this cancels such a profile.

Only a profile with no account behind it can be cancelled. Someone who
already has an account attached to the company is removed through the boot
route (`DELETE /companies/<id>/access/<user_id>`), which also clears their
access row, grants and project assignments.
"""

from __future__ import annotations

import dataclasses
from uuid import UUID

from app.application.companies._helpers import _assert_company_admin
from app.application.companies.ports import RoleCheckerPort, TransactionalSessionPort
from app.application.company_persons.exceptions import CompanyPersonNotFoundError, LinkedMemberNotCancellableError
from app.application.company_persons.ports import CompanyPersonRepositoryPort
from app.application.persons.ports import IPersonRepository


class CancelPendingMemberUseCase:
    def __init__(
        self,
        company_person_repo: CompanyPersonRepositoryPort,
        person_repo: IPersonRepository,
        role_checker: RoleCheckerPort,
    ) -> None:
        self._company_persons = company_person_repo
        self._persons = person_repo
        self._role_checker = role_checker

    def execute(self, caller_id: UUID, company_id: UUID, person_id: UUID, db_session: TransactionalSessionPort) -> None:
        _assert_company_admin(self._role_checker, caller_id, company_id)

        profile = self._company_persons.find(company_id, person_id)
        if profile is None or not profile.is_active:
            raise CompanyPersonNotFoundError(company_id, person_id)
        person = self._persons.find_by_id(person_id)
        if person is not None and person.user_id is not None:
            raise LinkedMemberNotCancellableError(company_id, person_id)

        # Inactive and no longer pending: hidden from the directory, and
        # sign-up linking (`list_pending_by_phone`) never attaches it.
        self._company_persons.save(
            dataclasses.replace(profile, is_active=False, pending_expires_at=None, pending_company_role=None)
        )
        db_session.commit()
