"""Who may validate attendance on a project — one SQL definition, two directions.

The bell (`SQLAlchemyPendingAttendanceQuery`, fixed user → which projects?) and
push targeting (`SQLAlchemyPushDeviceRepository`, fixed project → which users?)
must agree with `app.domain.authz.resolver` on `project:manage_labor`:

    company admin of the project's company
    OR company manager assigned to the project
    OR an explicit D8 grant of `project:manage_labor` (company-wide, or on this
       project)
    AND no D8 deny of the same permission in scope

Platform ops is handled by the callers (the bell shows ops everything; push
notifications are not sent to support staff). Legacy global/membership roles
are not consulted any more.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import and_, exists, not_, or_, select
from sqlalchemy.orm import Session, aliased

from app.infrastructure.database.models.associations import user_projects
from app.infrastructure.database.models.company_member_grant import CompanyMemberGrantModel
from app.infrastructure.database.models.user_company_access import UserCompanyAccessModel
from app.infrastructure.database.uuid_matching import is_sqlite, uuid_eq

MANAGE_LABOR = "project:manage_labor"


def _access_exists(sqlite: bool, user_col, company_col, role: str):
    return exists(
        select(1).where(
            uuid_eq(sqlite, UserCompanyAccessModel.user_id, user_col),
            uuid_eq(sqlite, UserCompanyAccessModel.company_id, company_col),
            UserCompanyAccessModel.role == role,
        )
    )


def _assignment_exists(sqlite: bool, user_col, project_col):
    return exists(
        select(1).where(
            uuid_eq(sqlite, user_projects.c.user_id, user_col),
            uuid_eq(sqlite, user_projects.c.project_id, project_col),
        )
    )


def _grant_exists(sqlite: bool, user_col, company_col, project_col, effect: str):
    """A grant/deny row applying to this project: company-wide or project-scoped."""
    return exists(
        select(1).where(
            uuid_eq(sqlite, CompanyMemberGrantModel.user_id, user_col),
            uuid_eq(sqlite, CompanyMemberGrantModel.company_id, company_col),
            CompanyMemberGrantModel.permission == MANAGE_LABOR,
            CompanyMemberGrantModel.effect == effect,
            or_(
                CompanyMemberGrantModel.project_id.is_(None),
                uuid_eq(sqlite, CompanyMemberGrantModel.project_id, project_col),
            ),
        )
    )


def may_validate_clause(session: Session, user_col, project_col, company_col):
    """SQL boolean: `user_col` may validate attendance on `project_col`.

    Columns may be literals (a fixed user or project) or correlated columns of
    the enclosing query, which is what lets the same definition serve both the
    bell and push targeting.
    """
    sqlite = is_sqlite(session)
    allowed = or_(
        _access_exists(sqlite, user_col, company_col, "admin"),
        and_(
            _access_exists(sqlite, user_col, company_col, "manager"),
            _assignment_exists(sqlite, user_col, project_col),
        ),
        _grant_exists(sqlite, user_col, company_col, project_col, "grant"),
    )
    return and_(allowed, not_(_grant_exists(sqlite, user_col, company_col, project_col, "deny")))


def validator_user_ids(session: Session, project_id: UUID, company_id: "UUID | None") -> "list[UUID]":
    """Every user who may validate attendance on one project (push targets).

    Empty when the project has no company: nobody holds a company role there,
    so nobody can validate — the same answer the resolver gives.
    """
    if company_id is None:
        return []
    sqlite = is_sqlite(session)
    # Alias the outer row: `may_validate_clause` queries the same table in its
    # EXISTS sub-selects, and without an alias SQLAlchemy would auto-correlate
    # them into a degenerate self-reference.
    caller = aliased(UserCompanyAccessModel)
    rows = session.execute(
        select(caller.user_id)
        .where(uuid_eq(sqlite, caller.company_id, company_id))
        .where(may_validate_clause(session, caller.user_id, project_id, company_id))
    ).all()
    return [row[0] for row in rows]
