"""Active company admins: the people who reach every project of a company.

A company admin opens and edits every project of their company whether or not a
`user_projects` row names them (rows exist only for admins present when a project was
created). Team lists and member counts therefore add them, so they match who can open
the project.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Iterable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session


def active_admin_ids_by_company(session: Session, company_ids: Iterable["UUID | str | None"]) -> "dict[str, set[str]]":
    """{company_id: {user_id, ...}} of the active admins of each company (ids as strings)."""
    from app.domain.companies.roles import CompanyRole
    from app.infrastructure.database.models import UserCompanyAccessModel, UserModel

    wanted = {UUID(str(c)) for c in company_ids if c}
    if not wanted:
        return {}
    rows = session.execute(
        select(UserCompanyAccessModel.company_id, UserCompanyAccessModel.user_id)
        .join(UserModel, UserModel.id == UserCompanyAccessModel.user_id)
        .where(
            UserCompanyAccessModel.company_id.in_(list(wanted)),
            UserCompanyAccessModel.role == CompanyRole.ADMIN.value,
            UserModel.is_active.is_(True),
        )
    ).all()
    out: "dict[str, set[str]]" = defaultdict(set)
    for company_id, user_id in rows:
        out[str(company_id)].add(str(user_id))
    return dict(out)


def team_size(assigned_user_ids: Iterable["UUID | str"], company_admin_ids: "set[str] | None") -> int:
    """Assigned people plus the company's active admins, each counted once."""
    return len({str(u) for u in assigned_user_ids} | (company_admin_ids or set()))
