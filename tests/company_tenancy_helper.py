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
* anyone else with a ``user_projects`` row → company ``member``;
* a user with neither ownership nor membership stays unattached — an outsider
  must still be refused.

Set ``users.is_platform_ops`` directly for a support persona; it is a flag on
the user row, never derived from anything here.

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


def company_for_projects(session, created_by, legal_name: str = "Test Tenancy Co") -> UUID:
    """Return a company id to hang a project on — `projects.company_id` is NOT NULL.

    Reuses the first company already in the database so a later
    :func:`seed_company_tenancy` call keeps everything in one tenant; creates
    one owned by `created_by` otherwise. Call it while building a fixture,
    before the project row is inserted.
    """
    from app.infrastructure.database.models.company import CompanyModel

    existing = session.query(CompanyModel).first()
    if existing is not None:
        return existing.id
    now = datetime.now(timezone.utc)
    company = CompanyModel(
        id=uuid4(),
        legal_name=legal_name,
        address="1 rue des Tests",
        created_by=created_by,
        created_at=now,
        updated_at=now,
    )
    session.add(company)
    session.flush()
    return company.id


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

        membership_rows = db.session.execute(text("SELECT user_id FROM user_projects")).fetchall()
        for (user_id,) in membership_rows:
            if derived.get(_key(user_id)) is None:
                derived[_key(user_id)] = "member"

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

        # Assign every project owner to their own project, like the production
        # backfill does.
        assigned = {
            (_key(u), _key(p))
            for u, p in db.session.execute(text("SELECT user_id, project_id FROM user_projects")).fetchall()
        }
        for project in projects:
            if project.owner_id is None or (_key(project.owner_id), _key(project.id)) in assigned:
                continue
            db.session.execute(
                text(
                    "INSERT INTO user_projects (user_id, project_id, invited_by_user_id, assigned_at) "
                    "VALUES (:uid, :pid, NULL, :at)"
                ),
                {"uid": str(project.owner_id), "pid": str(project.id), "at": now},
            )

        db.session.commit()

        # Creator assignments + directory profiles: the same code the production
        # migration runs, so fixtures and deploys agree on the mapping.
        from app.infrastructure.database.backfills.platform_ops_and_creator_assignments import run_backfill

        run_backfill(db.session.connection())
        db.session.commit()

    return company_id


def relax_projects_company_id(engine) -> None:
    """Recreate `projects` with a nullable `company_id`, as it was pre-migration.

    Migration c2b8f1a0d743 makes the column NOT NULL and the model says so, but
    the data steps of the revisions BEFORE it run against databases that still
    have orphan projects. A test covering those steps needs the old shape;
    call :func:`restore_projects_company_id` when the module is done.
    """
    from app.infrastructure.database.models import ProjectModel

    ProjectModel.__table__.c.company_id.nullable = True
    ProjectModel.__table__.drop(engine, checkfirst=True)
    ProjectModel.__table__.create(engine)


def restore_projects_company_id(engine) -> None:
    """Undo :func:`relax_projects_company_id` — the constraint is back."""
    from app.infrastructure.database.models import ProjectModel

    ProjectModel.__table__.c.company_id.nullable = False
    ProjectModel.__table__.drop(engine, checkfirst=True)
    ProjectModel.__table__.create(engine)
