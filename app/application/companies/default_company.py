"""Which company a request with no company context applies to.

Company-scoped reads and writes (labor roles, library, inventory) take an explicit
``company_id`` from every current client. Older clients send none; for them the
default is the caller's primary company, otherwise their oldest attachment — the
same company ``GET /companies`` lists first (primary first, then by attachment
date), so the API and the clients' "first company" agree. Accounts added to a
company by an admin are never primary, so a user in two such companies has no
primary row at all: before this, those endpoints fell back to company-less legacy
rows or answered 403.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID


def default_company_id(authz_reader: Any | None, access_repo: Any | None, user_id: UUID) -> UUID | None:
    """The caller's primary company, else their oldest attachment, else None."""
    if authz_reader is not None:
        primary: UUID | None = authz_reader.primary_company_id(user_id)
        if primary is not None:
            return primary
    if access_repo is not None:
        rows = access_repo.list_for_user(user_id)
        if rows:
            # list_for_user orders primary first, then oldest attachment first.
            company_id: UUID | None = rows[0].company_id
            return company_id
    return None
