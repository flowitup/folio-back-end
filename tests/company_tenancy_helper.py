"""Give an API test fixture the company tenancy the permission resolver needs.

Permissions come from `user_company_access.role` + `user_projects` (+ D8 rows),
so a fixture that seeds only users and projects grants nothing at all. Rather
than repeating twenty lines of company/access/assignment seeding in every test
module, call :func:`seed_company_tenancy` once after the fixture has committed
its users and projects.

Roles are derived from what the fixture already expresses, so an existing test
keeps its intent:

* the owner of a project → company ``manager`` (and assigned to it, mirroring
  the creator backfill the platform-ops migration performs);
* a ``user_projects`` row whose legacy role is named ``admin``/``manager`` →
  company ``manager``; any other membership → company ``member``;
* a user holding a legacy global role that carries ``*:*`` →
  ``users.is_platform_ops`` (the support flag, not a tenant role);
* a user with neither ownership nor membership stays unattached — an outsider
  must still be refused.

Pass ``roles={user_id: "admin"}`` to override the derivation (the common case:
the fixture's "admin" persona has to be a company admin, not just a manager).
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import text

_ROLE_RANK = {"member": 0, "manager": 1, "admin": 2}


def _first(rows):
    return rows[0] if rows else None


def _key(value) -> str:
    """Insert-path-agnostic id key: raw SQL returns dashless hex on SQLite, the ORM dashed."""
    return str(value).replace("-", "").lower()


def seed_company_tenancy(
    app,
    *,
    legal_name: str = "Test Tenancy Co",
    roles: "dict | None" = None,
    company_id: "UUID | None" = None,
) -> UUID:
    """Attach every project and its people to one company. Returns the company id.

    Args:
        app: the Flask app whose (already seeded) database to update.
        legal_name: name of the company to create when there is none to reuse.
        roles: explicit ``{user_id: "admin"|"manager"|"member"}`` overrides.
        company_id: attach to this existing company instead of creating one.
    """
    from app import db
    from app.infrastructure.database.models import ProjectModel, UserModel
    from app.infrastructure.database.models.company import CompanyModel
    from app.infrastructure.database.models.user_company_access import UserCompanyAccessModel

    overrides = {_key(k): v for k, v in (roles or {}).items()}
    now = datetime.now(timezone.utc)

    with app.app_context():
        projects = db.session.query(ProjectModel).all()
        users = db.session.query(UserModel).all()

        if company_id is None:
            existing = _first(db.session.query(CompanyModel).all())
            if existing is not None:
                company_id = existing.id
            else:
                creator = _first([p.owner_id for p in projects] or [u.id for u in users])
                company = CompanyModel(
                    id=uuid4(),
                    legal_name=legal_name,
                    address="1 rue des Tests",
                    created_by=creator,
                    created_at=now,
                    updated_at=now,
                )
                db.session.add(company)
                db.session.flush()
                company_id = company.id

        for project in projects:
            if project.company_id is None:
                project.company_id = company_id

        # Derive a role per user from ownership and membership.
        derived: "dict[str, str]" = {}
        for project in projects:
            if project.owner_id is not None:
                derived[_key(project.owner_id)] = "manager"

        membership_rows = db.session.execute(
            text(
                "SELECT up.user_id, r.name FROM user_projects up "
                "LEFT JOIN roles r ON CAST(r.id AS TEXT) = CAST(up.role_id AS TEXT)"
            )
        ).fetchall()
        for user_id, role_name in membership_rows:
            role = "manager" if (role_name or "").lower() in ("admin", "manager") else "member"
            current = derived.get(_key(user_id))
            if current is None or _ROLE_RANK[role] > _ROLE_RANK[current]:
                derived[_key(user_id)] = role

        derived.update(overrides)

        for user in users:
            role = derived.get(_key(user.id))
            if role is None:
                continue
            access = db.session.get(UserCompanyAccessModel, (user.id, company_id))
            if access is None:
                db.session.add(
                    UserCompanyAccessModel(
                        user_id=user.id,
                        company_id=company_id,
                        role=role,
                        is_primary=True,
                        attached_at=now,
                    )
                )
            elif _ROLE_RANK[role] > _ROLE_RANK.get(access.role, 0):
                access.role = role

        # Assign every project owner to their own project (role_id NULL: the
        # legacy column grants nothing). The production backfill does the same,
        # but it references a legacy role row that a test fixture may not seed.
        assigned = {
            (_key(u), _key(p))
            for u, p in db.session.execute(text("SELECT user_id, project_id FROM user_projects")).fetchall()
        }
        for project in projects:
            if project.owner_id is None or (_key(project.owner_id), _key(project.id)) in assigned:
                continue
            db.session.execute(
                text(
                    "INSERT INTO user_projects (user_id, project_id, role_id, invited_by_user_id, assigned_at) "
                    "VALUES (:uid, :pid, NULL, NULL, :at)"
                ),
                {"uid": str(project.owner_id), "pid": str(project.id), "at": now},
            )

        db.session.commit()

        # Ops flag + directory profiles: the same code the production migration
        # runs, so fixtures and deploys agree on the mapping.
        from app.infrastructure.database.backfills.platform_ops_and_creator_assignments import run_backfill

        run_backfill(db.session.connection())
        db.session.commit()

    return company_id
