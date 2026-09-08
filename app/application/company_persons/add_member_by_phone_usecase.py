"""AddMemberByPhoneUseCase — company admin onboards a person by phone number.

Match order (Phase 2 onboarding, finding 5):
  (a) an existing user account with this phone → attach immediately
      (`user_company_access` + `company_persons`, creating a `Person` if the
      account has none linked yet).
  (b) an un-linked `Person` (no `user_id`) with the same normalized phone,
      already profiled in a company the caller admins → link/copy that
      profile into the target company as a pending row.
  (c) more than one such candidate → 409, caller resends with `person_id`.
  (d) no match at all → create a brand new `Person` + pending `company_persons`
      row (`pending_expires_at = now + 30 days`); it links automatically the
      next time someone signs up with this phone (`VerifySignupOtpUseCase`).

The response never reveals which branch fired beyond "pending vs already
linked" is not even exposed — (a) and (d) return the identical shape.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID, uuid4

from app.application.companies._helpers import _assert_company_admin
from app.application.companies.ports import (
    CompanyRepositoryPort,
    RoleCheckerPort,
    TransactionalSessionPort,
    UserCompanyAccessRepositoryPort,
)
from app.application.company_persons.exceptions import (
    AdminRoleNotAssignableError,
    InvalidCandidatePersonError,
    MemberAlreadyAttachedError,
    MultipleCandidatesError,
)
from app.application.company_persons.dtos import AddMemberByPhoneInput, AddMemberByPhoneResult
from app.application.company_persons.ports import CompanyPersonRepositoryPort
from app.application.persons.ports import IPersonRepository
from app.application.ports.user_repository import UserRepositoryPort
from app.domain.companies.exceptions import CompanyNotFoundError
from app.domain.companies.roles import CompanyRole
from app.domain.companies.user_company_access import UserCompanyAccess
from app.domain.entities.company_person import CompanyPerson
from app.domain.entities.person import Person
from app.domain.value_objects.phone_number import InvalidPhoneNumberError, normalize_phone

_PENDING_WINDOW_DAYS = 30
_ASSIGNABLE_ROLES = (CompanyRole.MEMBER.value, CompanyRole.MANAGER.value)


class AddMemberByPhoneUseCase:
    def __init__(
        self,
        company_repo: CompanyRepositoryPort,
        access_repo: UserCompanyAccessRepositoryPort,
        person_repo: IPersonRepository,
        company_person_repo: CompanyPersonRepositoryPort,
        user_repo: UserRepositoryPort,
        role_checker: RoleCheckerPort,
    ) -> None:
        self._companies = company_repo
        self._access = access_repo
        self._persons = person_repo
        self._company_persons = company_person_repo
        self._users = user_repo
        self._role_checker = role_checker

    def execute(
        self,
        inp: AddMemberByPhoneInput,
        db_session: TransactionalSessionPort,
    ) -> AddMemberByPhoneResult:
        _assert_company_admin(self._role_checker, inp.caller_id, inp.company_id)

        company = self._companies.find_by_id(inp.company_id)
        if company is None:
            raise CompanyNotFoundError(inp.company_id)

        if inp.role not in _ASSIGNABLE_ROLES:
            raise AdminRoleNotAssignableError(f"role must be one of {_ASSIGNABLE_ROLES}, got {inp.role!r}")

        try:
            phone = normalize_phone(inp.phone, default_region=company.default_phone_region)
        except InvalidPhoneNumberError as exc:
            raise ValueError(str(exc)) from exc

        now = datetime.now(timezone.utc)

        # ------------------------------------------------------------------
        # (a) existing user account with this phone.
        # ------------------------------------------------------------------
        user = self._users.find_by_phone(phone)
        if user is not None:
            if self._access.find(user.id, inp.company_id) is not None:
                raise MemberAlreadyAttachedError(f"user {user.id} is already attached to company {inp.company_id}")

            person = self._persons.find_by_user_id(user.id)
            if person is None:
                person = self._persons.create(
                    Person(
                        id=uuid4(),
                        name=(inp.name or user.display_name or phone).strip(),
                        normalized_name=Person.normalize(inp.name or user.display_name or phone),
                        created_by_user_id=inp.caller_id,
                        created_at=now,
                        phone=phone,
                        phone_normalized=phone,
                        user_id=user.id,
                    )
                )
                self._persons.set_user_id(person.id, user.id)

            self._access.save(
                UserCompanyAccess(
                    user_id=user.id,
                    company_id=inp.company_id,
                    is_primary=len(self._access.list_for_user(user.id)) == 0,
                    attached_at=now,
                    role=inp.role,
                )
            )
            self._upsert_company_person(inp.company_id, person.id, inp.caller_id, phone, now, pending=False)
            db_session.commit()
            return AddMemberByPhoneResult(person_id=person.id, name=person.name, phone=phone, pending=False)

        # ------------------------------------------------------------------
        # Caller resent with an explicit person_id after a 409 (match order c).
        # ------------------------------------------------------------------
        if inp.person_id is not None:
            chosen = self._persons.find_by_id(inp.person_id)
            if chosen is None or chosen.user_id is not None:
                raise InvalidCandidatePersonError(f"person {inp.person_id} is not a valid candidate")
            return self._create_pending(inp, company, chosen, phone, now, db_session)

        # ------------------------------------------------------------------
        # (b)/(c) un-linked Person profiled in a company the caller admins.
        # ------------------------------------------------------------------
        admin_company_ids = [a.company_id for a in self._access.list_for_user(inp.caller_id) if a.role == "admin"]
        candidates: dict[UUID, Person] = {}
        for admin_cid in admin_company_ids:
            cp = self._company_persons.find_by_phone(admin_cid, phone)
            if cp is None:
                continue
            candidate_person = self._persons.find_by_id(cp.person_id)
            if candidate_person is not None and candidate_person.user_id is None:
                candidates[candidate_person.id] = candidate_person

        if len(candidates) > 1:
            raise MultipleCandidatesError(
                candidates=[{"person_id": str(p.id), "name": p.name} for p in candidates.values()]
            )

        chosen: Optional[Person] = next(iter(candidates.values()), None)

        # ------------------------------------------------------------------
        # (d) no match at all → brand new Person.
        # ------------------------------------------------------------------
        if chosen is None:
            chosen = self._persons.create(
                Person(
                    id=uuid4(),
                    name=(inp.name or phone).strip(),
                    normalized_name=Person.normalize(inp.name or phone),
                    created_by_user_id=inp.caller_id,
                    created_at=now,
                    phone=phone,
                    phone_normalized=phone,
                )
            )

        return self._create_pending(inp, company, chosen, phone, now, db_session)

    # ----------------------------------------------------------------------

    def _create_pending(
        self, inp, company, person: Person, phone: str, now: datetime, db_session
    ) -> AddMemberByPhoneResult:
        self._upsert_company_person(inp.company_id, person.id, inp.caller_id, phone, now, pending=True)
        db_session.commit()
        return AddMemberByPhoneResult(person_id=person.id, name=person.name, phone=phone, pending=True)

    def _upsert_company_person(
        self,
        company_id: UUID,
        person_id: UUID,
        created_by_user_id: UUID,
        phone: str,
        now: datetime,
        *,
        pending: bool,
    ) -> CompanyPerson:
        existing = self._company_persons.find(company_id, person_id)
        if existing is not None:
            return existing
        return self._company_persons.save(
            CompanyPerson(
                id=uuid4(),
                company_id=company_id,
                person_id=person_id,
                created_at=now,
                is_active=True,
                phone_normalized=phone,
                pending_expires_at=(now + timedelta(days=_PENDING_WINDOW_DAYS)) if pending else None,
                created_by_user_id=created_by_user_id,
            )
        )
