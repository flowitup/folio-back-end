"""LinkPersonOnSignupUseCase — attach a fresh account to pending onboarding profiles.

Called right after a NEW user account is created and its phone number is
known (phone-OTP sign-up, or an invitation accepted by someone who supplied
a phone). Finds every non-expired pending `company_persons` profile whose
`phone_normalized` matches the verified phone, links the underlying `Person`
to the new account (once — never overwrites an existing different link),
and creates a `member` `user_company_access` row for each of those
companies. Clears `pending_expires_at` so a later re-run is a no-op.

No enumeration: this runs unconditionally after account creation, so the
caller-visible response of the sign-up flow is identical whether or not any
pending profile existed for this phone.
"""

from __future__ import annotations

import dataclasses
import logging
from datetime import datetime, timezone
from typing import List
from uuid import UUID

from app.application.companies.ports import UserCompanyAccessRepositoryPort
from app.application.company_persons.ports import CompanyPersonRepositoryPort
from app.application.persons.ports import IPersonRepository
from app.domain.companies.roles import CompanyRole
from app.domain.companies.user_company_access import UserCompanyAccess

_log = logging.getLogger(__name__)


class LinkPersonOnSignupUseCase:
    def __init__(
        self,
        person_repo: IPersonRepository,
        company_person_repo: CompanyPersonRepositoryPort,
        access_repo: UserCompanyAccessRepositoryPort,
    ) -> None:
        self._persons = person_repo
        self._company_persons = company_person_repo
        self._access = access_repo

    def execute(self, user_id: UUID, phone_normalized: str) -> List[UUID]:
        """Link every non-expired pending profile for `phone_normalized` to `user_id`.

        Returns the list of company ids the user was newly attached to.
        Never raises — a malformed/missing pending row is skipped and logged
        so one bad row cannot break the sign-up flow.
        """
        now = datetime.now(timezone.utc)
        pending = self._company_persons.list_pending_by_phone(phone_normalized, now)
        linked_company_ids: List[UUID] = []

        for profile in pending:
            person = self._persons.find_by_id(profile.person_id)
            if person is None:
                _log.warning("link_person_on_signup: pending profile %s has no Person row", profile.id)
                continue

            if person.user_id is None:
                self._persons.set_user_id(person.id, user_id)
            elif person.user_id != user_id:
                # Recycled-number safety valve (finding 5): this pending row was
                # created for a different account than the one that just verified
                # this phone — do not silently steal the identity link.
                _log.warning(
                    "link_person_on_signup: person %s already linked to a different user; skipping",
                    person.id,
                )
                continue

            if self._access.find(user_id, profile.company_id) is None:
                self._access.save(
                    UserCompanyAccess(
                        user_id=user_id,
                        company_id=profile.company_id,
                        is_primary=len(self._access.list_for_user(user_id)) == 0,
                        attached_at=now,
                        role=CompanyRole.MEMBER.value,
                    )
                )
                linked_company_ids.append(profile.company_id)
                _log.info(
                    "link_person_on_signup: user=%s linked to company=%s via pending profile=%s",
                    user_id,
                    profile.company_id,
                    profile.id,
                )

            # Clear the pending window regardless — the profile is now resolved
            # (either just attached, or the user already had access some other way).
            self._company_persons.save(dataclasses.replace(profile, pending_expires_at=None))

        return linked_company_ids
