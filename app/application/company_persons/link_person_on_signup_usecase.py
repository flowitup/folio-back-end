"""LinkPersonOnSignupUseCase — attach a fresh account to pending onboarding profiles.

Called right after a NEW user account is created and its phone number is
known (phone-OTP sign-up, or an invitation accepted by someone who supplied
a phone). Finds every non-expired pending `company_persons` profile whose
`phone_normalized` matches the verified phone, links the underlying `Person`
to the new account, and creates a `member` `user_company_access` row for
each of those companies. Clears `pending_expires_at` so a later re-run is a
no-op.

Multi-company pending profiles (C1): two different companies' admins, with
no visibility into each other, can independently add the same phone number
before it ever signs up — each creates its OWN unlinked `Person` row. When
several such pending profiles match the verified phone, exactly ONE Person
survives as the account's identity:
  - the user's own Person if they already have one (`find_by_user_id` —
    e.g. they were attached earlier via an accepted invitation), otherwise
  - the earliest-created (`persons.created_at`) Person among the matches.
Every other matching profile is repointed to the survivor's `person_id`.
A duplicate Person left with no remaining `company_persons` row is deleted
ONLY if nothing else references it (no `Worker` row) — otherwise it is left
unlinked for ops review rather than risking a destructive delete.

No enumeration: this runs unconditionally after account creation, so the
caller-visible response of the sign-up flow is identical whether or not any
pending profile existed for this phone.

CAN RAISE: an unexpected repository failure (e.g. a DB error while
repointing or deleting a duplicate) is not swallowed here — the OTP/
invitation call site wraps this call in `try/except Exception` with logging
so sign-up itself never fails on a broken pending row (see
`app.application.usecases.otp_login.VerifySignupOtpUseCase`).
"""

from __future__ import annotations

import dataclasses
import logging
from datetime import datetime, timezone
from typing import List, Tuple
from uuid import UUID

from app.application.companies.ports import UserCompanyAccessRepositoryPort
from app.application.company_persons.ports import CompanyPersonRepositoryPort
from app.application.persons.ports import IPersonRepository
from app.domain.companies.roles import CompanyRole
from app.domain.companies.user_company_access import UserCompanyAccess
from app.domain.entities.company_person import CompanyPerson
from app.domain.entities.person import Person

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
        A malformed/missing pending row (no resolvable Person, or a Person
        already linked to a DIFFERENT account) is skipped and logged so one
        bad row cannot block the others.
        """
        now = datetime.now(timezone.utc)
        pending = self._company_persons.list_pending_by_phone(phone_normalized, now)
        if not pending:
            return []

        eligible: List[Tuple[CompanyPerson, Person]] = []
        for profile in pending:
            person = self._persons.find_by_id(profile.person_id)
            if person is None:
                _log.warning("link_person_on_signup: pending profile %s has no Person row", profile.id)
                continue
            if person.user_id is not None and person.user_id != user_id:
                # Recycled-number safety valve (finding 5): this pending row was
                # created for a different account than the one that just verified
                # this phone — do not silently steal the identity link.
                _log.warning(
                    "link_person_on_signup: person %s already linked to a different user; skipping",
                    person.id,
                )
                continue
            eligible.append((profile, person))

        if not eligible:
            return []

        # C1: pick ONE survivor Person across every matching company. Prefer
        # the signing-up user's own Person if one already exists (e.g. an
        # earlier accepted invitation); otherwise the earliest-created
        # candidate among this batch — a stable, deterministic choice.
        survivor = self._persons.find_by_user_id(user_id)
        if survivor is None:
            survivor = min((person for _, person in eligible), key=lambda p: p.created_at)

        if survivor.user_id is None:
            self._persons.set_user_id(survivor.id, user_id, commit=False)

        linked_company_ids: List[UUID] = []
        duplicate_person_ids: set = set()

        for profile, person in eligible:
            if person.id != survivor.id:
                # Repoint this company's profile at the survivor instead of
                # its own now-redundant Person row.
                updated_profile = dataclasses.replace(profile, person_id=survivor.id, pending_expires_at=None)
                duplicate_person_ids.add(person.id)
            else:
                updated_profile = dataclasses.replace(profile, pending_expires_at=None)
            self._company_persons.save(updated_profile)

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

        self._cleanup_duplicates(duplicate_person_ids)
        return linked_company_ids

    # ------------------------------------------------------------------

    def _cleanup_duplicates(self, duplicate_person_ids: "set[UUID]") -> None:
        """Delete a duplicate Person left orphaned by the repoint above.

        Only when it has NO remaining `company_persons` row (it may still be
        profiled in another company that shares this same duplicate, e.g. one
        admin managing two companies reused the same unlinked Person) AND no
        `Worker` row references it. Otherwise it is left unlinked — a human
        can merge it later via `MergePersonsUseCase` — rather than risking a
        destructive delete of a row that is still in use.
        """
        for duplicate_id in duplicate_person_ids:
            if self._company_persons.list_for_person(duplicate_id):
                continue
            if self._persons.has_references(duplicate_id):
                _log.info(
                    "link_person_on_signup: duplicate person %s still referenced by workers; left unlinked",
                    duplicate_id,
                )
                continue
            self._persons.delete(duplicate_id)
