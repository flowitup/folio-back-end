"""Self-service account deletion (App Store guideline 5.1.1(v)).

Deletion anonymizes rather than removes. ``users.id`` is referenced by 36 foreign
keys: ``companies.created_by``, ``projects.owner_id``, notes, project documents,
photos and analyses are RESTRICT/NOT NULL, so a real DELETE fails outright for any
user who has done anything — and ``billing_documents.created_by`` and
``chat_messages.sender_id`` CASCADE, so forcing it through would destroy company
records the company is legally required to keep.

So the user row survives stripped of every personal field, the rows that *are*
personal (credentials, devices, access grants) are deleted outright, and the
company's own data is left exactly as it was.
"""

import logging
from typing import Protocol
from uuid import UUID

from app.application.companies.ports import (
    ClockPort,
    CompanyRepositoryPort,
    UserCompanyAccessRepositoryPort,
)
from app.application.invitations.ports import TransactionalSessionPort
from app.application.ports.user_repository import UserRepositoryPort
from app.domain.companies.roles import CompanyRole

logger = logging.getLogger(__name__)


class AccountNotFoundError(Exception):
    """Raised when the authenticated user has no user row (already erased)."""


class DeletionBlockedByLastAdminError(Exception):
    """Raised when erasing the caller would leave a shared company with no admin.

    Deliberately NOT named LastCompanyAdminError: that name is already taken by
    the domain exception the demote/boot/detach use-cases raise, with a different
    constructor. Two same-named exceptions with incompatible arities is a trap —
    an `except` for one silently fails to catch the other.

    Carries the company's name so the client can tell the user exactly which
    company needs another admin before they can delete their account.
    """

    def __init__(self, company_id: UUID, company_name: str) -> None:
        self.company_id = company_id
        self.company_name = company_name
        super().__init__(f"User is the last admin of company {company_name} ({company_id})")


class PersonalDataEraserPort(Protocol):
    """Deletes the rows that belong to a person rather than to their company."""

    def erase_for_user(self, user_id: UUID) -> None:
        """Delete every personal/credential row owned by ``user_id``.

        Covers access grants, devices and credentials — never business records.
        Implementations must be idempotent: erasing twice is not an error.
        """
        ...


class DeleteAccountUseCase:
    """Erase the caller's own account, keeping their company's records intact."""

    def __init__(
        self,
        user_repo: UserRepositoryPort,
        access_repo: UserCompanyAccessRepositoryPort,
        company_repo: CompanyRepositoryPort,
        eraser: PersonalDataEraserPort,
        db_session: TransactionalSessionPort,
        clock: ClockPort,
    ) -> None:
        self._users = user_repo
        self._access = access_repo
        self._companies = company_repo
        self._eraser = eraser
        self._db = db_session
        self._clock = clock

    def execute(self, user_id: UUID) -> None:
        """Anonymize ``user_id`` and delete their personal rows, in one transaction.

        Raises:
            AccountNotFoundError: no such user, or already erased.
            LastCompanyAdminError: the caller is the only admin left in a company
                that still has other members.
        """
        user = self._users.find_by_id(user_id)
        if user is None or user.is_deleted:
            raise AccountNotFoundError(f"No account for {user_id}")

        accesses = self._access.list_for_user(user_id)
        orphaned = self._reject_if_last_admin_of_a_shared_company(user_id, accesses)

        self._users.save(user.anonymized(self._clock.now()))
        self._eraser.erase_for_user(user_id)
        if orphaned:
            # D2 lets a solo user leave, which can strand a company with records
            # and no members. Nothing cleans that up, so leave ops a trail.
            logger.warning(
                "account erased; company left with no members: company_ids=%s",
                ",".join(str(c) for c in orphaned),
            )
        self._db.commit()

    def _reject_if_last_admin_of_a_shared_company(
        self, user_id: UUID, accesses: list
    ) -> list:
        """Block the erasure when it would strand other members without an admin.

        A solo user is always allowed to leave — their company has nobody left to
        strand, and blocking them would make the account undeletable, which is the
        thing the App Store guideline forbids.

        ``list_admins_for_update`` takes a row lock, so two admins deleting their
        accounts at the same moment cannot both read "two admins" and both proceed.
        Row locks do not block INSERTs, so a member joining in the same instant a
        solo admin deletes can still slip past — see the known-limitations note in
        the feature's decision record.

        Returns the companies this erasure leaves with no members at all.
        """
        # Sorted by company_id so two users who administer the same companies take
        # the row locks in the same order; iterating in per-user order instead
        # deadlocks when they delete their accounts concurrently.
        admin_accesses = sorted(
            (a for a in accesses if a.role == CompanyRole.ADMIN.value),
            key=lambda a: str(a.company_id),
        )
        orphaned: list = []
        for access in admin_accesses:
            admins = self._access.list_admins_for_update(access.company_id)
            other_admins = [a for a in admins if a.user_id != user_id]
            if other_admins:
                continue
            members = self._access.list_for_company(access.company_id)
            other_members = [m for m in members if m.user_id != user_id]
            if not other_members:
                # Nobody to strand. The company keeps its records but loses its
                # last member; the caller logs that so ops can find it.
                orphaned.append(access.company_id)
                continue
            raise DeletionBlockedByLastAdminError(
                access.company_id, self._company_name(access.company_id)
            )
        return orphaned

    def _company_name(self, company_id: UUID) -> str:
        company = self._companies.find_by_id(company_id)
        return company.legal_name if company is not None else ""
