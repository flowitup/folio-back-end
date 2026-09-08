"""Invariant: every company attachment has an active, user-linked directory profile.

The assign-member pickers (web and mobile) list `company_persons`, so a user who
holds a `user_company_access` row without a profile is attached but invisible —
an admin cannot put them on a project. Every path that attaches a user to a
company calls :func:`ensure_company_person`; the platform-ops migration
backfills the ones that predate it.

Repository-shaped arguments are optional so a caller wired without the directory
repositories (a minimal test fixture) simply skips the profile instead of
failing the attachment it was actually asked to perform.
"""

from __future__ import annotations

import dataclasses
from datetime import datetime
from typing import Any, Optional
from uuid import UUID, uuid4

from app.domain.entities.company_person import CompanyPerson
from app.domain.entities.person import Person


def ensure_company_person(
    *,
    persons: Optional[Any],
    company_persons: Optional[Any],
    users: Optional[Any],
    user_id: UUID,
    company_id: UUID,
    now: datetime,
    commit: bool = False,
) -> None:
    """Create (or reactivate) the directory profile linking `user_id` to `company_id`.

    Args:
        persons: PersonRepositoryPort — resolves/creates the global identity.
        company_persons: CompanyPersonRepositoryPort — the per-company profile.
        users: UserRepositoryPort — source of the display name/phone when the
            user has no `persons` row yet.
        user_id: the newly attached user.
        company_id: the company they were attached to.
        now: creation timestamp.
        commit: passed through to `persons.create` for callers that own their
            transaction boundary.
    """
    if persons is None or company_persons is None:
        return

    person = persons.find_by_user_id(user_id)
    if person is None:
        if users is None:
            return
        user = users.find_by_id(user_id)
        if user is None:
            return
        display_name = user.display_name or user.email
        person = persons.create(
            Person(
                id=uuid4(),
                name=display_name,
                normalized_name=Person.normalize(display_name),
                created_by_user_id=user_id,
                created_at=now,
                phone=user.phone,
                phone_normalized=user.phone,
                user_id=user_id,
            ),
            commit=commit,
        )

    existing = company_persons.find(company_id, person.id)
    if existing is not None:
        # A previously booted profile is reactivated; an active one is untouched.
        if not existing.is_active:
            company_persons.save(dataclasses.replace(existing, is_active=True, pending_expires_at=None))
        return

    company_persons.save(
        CompanyPerson(
            id=uuid4(),
            company_id=company_id,
            person_id=person.id,
            created_at=now,
            is_active=True,
            phone_normalized=_free_phone(company_persons, company_id, person),
            created_by_user_id=user_id,
        )
    )


def _free_phone(company_persons: Any, company_id: UUID, person) -> Optional[str]:
    """The person's phone, or None when this company already has a profile using it.

    PostgreSQL enforces a partial `UNIQUE(company_id, phone_normalized)` on
    `company_persons` (migration `2ca24be9e3a8`; SQLite does not, so the test
    suite cannot reproduce it). An admin who pre-added this human by phone
    already owns that number in the company, and attaching a user must not
    fail with an IntegrityError over it — the attachment is what the caller
    asked for. The two rows stay separate identities until someone merges them
    from the directory.
    """
    phone = getattr(person, "phone_normalized", None)
    find_by_phone = getattr(company_persons, "find_by_phone", None)
    if phone is None or find_by_phone is None:
        return phone
    return None if find_by_phone(company_id, phone) is not None else phone
