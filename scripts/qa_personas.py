"""Create the three QA personas (admin / manager / member) of a QA company.

Every persona set is tagged by a suffix: the company is named
``Folio QA <suffix>`` and nothing outside that company is ever touched, so
several QA runs can coexist and `--delete` can never reach production data.
The CLI lives in ``scripts/qa_personas_cli.py``:

    QA_PASSWORD='…' uv run python -m scripts.qa_personas --create --suffix smoke \\
        --admin-email qa.admin@example.com \\
        --manager-email qa.manager@example.com \\
        --member-email qa.member@example.com

This script is run against PRODUCTION, so `--create` never takes over an
account it did not make: an email that already exists is refused unless that
user is already attached to this QA company, and a pre-existing user's
password, activation state and primary company are left exactly as they were.

`--create` is idempotent: re-running finds the company, the users, the
attachments, the project and the assignments it made last time.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import text

from app.infrastructure.database.backfills.authz_backfill_report import BackfillReport
from app.infrastructure.database.backfills.directory_profiles import ensure_directory_profile
from scripts.qa_personas_guards import (
    QaPersonaRefused as QaPersonaRefused,  # re-exported: the create-side guard error
    attached_company_ids,
    find_company,
    find_user,
    refuse_accounts_we_do_not_own,
)
from scripts.qa_personas_purge import id_param, id_sql

COMPANY_PREFIX = "Folio QA "
DEFAULT_ADDRESS = "1 rue de la Recette, 75000 Paris"

_COMPANY_ACCESS_SQL = text(f"SELECT user_id, company_id FROM user_company_access WHERE {id_sql('company_id')} = :cid")


def company_name(suffix: str) -> str:
    return f"{COMPANY_PREFIX}{suffix.strip()}"


def project_name(suffix: str) -> str:
    return f"Folio QA Project {suffix.strip()}"


def _ensure_user(email: str, password_hash: str, display_name: str):
    """Find the user, or create them. A pre-existing row is never modified."""
    from app import db
    from app.infrastructure.database.models import UserModel

    user = find_user(email)
    if user is not None:
        return user
    user = UserModel(
        email=email.lower(),
        password_hash=password_hash,
        display_name=display_name,
        is_active=True,
    )
    db.session.add(user)
    db.session.flush()
    return user


def _ensure_company(suffix: str, address: str, admin_user):
    from app import db
    from app.infrastructure.database.models import CompanyModel

    name = company_name(suffix)
    company = find_company(name)
    if company is not None:
        return company
    now = datetime.now(timezone.utc)
    company = CompanyModel(
        legal_name=name,
        address=address,
        created_by=admin_user.id,
        created_at=now,
        updated_at=now,
    )
    db.session.add(company)
    db.session.flush()
    return company


def _ensure_access(company, user, role: str) -> None:
    """Attach the user to the QA company. `is_primary` only if they have no other."""
    from app import db
    from app.infrastructure.database.models import UserCompanyAccessModel

    access = db.session.get(UserCompanyAccessModel, (user.id, company.id))
    if access is not None:
        access.role = role
        return
    others = [cid for cid in attached_company_ids(user.id) if id_param(cid) != id_param(company.id)]
    db.session.add(
        UserCompanyAccessModel(
            user_id=user.id,
            company_id=company.id,
            role=role,
            # A second is_primary row would trip the partial unique index.
            is_primary=not others,
            attached_at=datetime.now(timezone.utc),
        )
    )


def _ensure_project(suffix: str, company, owner):
    from app import db
    from app.infrastructure.database.models import ProjectModel

    name = project_name(suffix)
    project = db.session.query(ProjectModel).filter_by(name=name, company_id=company.id).first()
    if project is not None:
        return project
    project = ProjectModel(name=name, address=DEFAULT_ADDRESS, owner_id=owner.id, company_id=company.id)
    db.session.add(project)
    db.session.flush()
    return project


def _ensure_assignment(project, user) -> None:
    from app import db
    from app.infrastructure.database.models import user_projects

    existing = db.session.execute(
        user_projects.select().where((user_projects.c.user_id == user.id) & (user_projects.c.project_id == project.id))
    ).first()
    if existing is not None:
        return
    db.session.execute(
        user_projects.insert().values(
            user_id=user.id,
            project_id=project.id,
            invited_by_user_id=None,
            assigned_at=datetime.now(timezone.utc),
        )
    )


def create_personas(suffix: str, emails: dict[str, str], password: str, address: str) -> dict[str, UUID]:
    """Create (or find) the QA company, its three personas and its project.

    Returns the ids worth pasting into a QA session. Raises `QaPersonaRefused`
    before writing anything when an email belongs to an account outside this
    QA company.
    """
    from app import db
    from app.infrastructure.adapters.argon2_hasher import Argon2PasswordHasher

    name = company_name(suffix)
    refuse_accounts_we_do_not_own(emails, find_company(name), name)

    password_hash = Argon2PasswordHasher().hash(password)
    users = {
        role: _ensure_user(emails[role], password_hash, f"QA {role.capitalize()} {suffix}")
        for role in ("admin", "manager", "member")
    }

    company = _ensure_company(suffix, address, users["admin"])
    for role, user in users.items():
        _ensure_access(company, user, role)
    db.session.flush()

    project = _ensure_project(suffix, company, users["admin"])
    for role in ("admin", "manager", "member"):
        _ensure_assignment(project, users[role])

    # Directory profiles, through the same helper the production backfill uses.
    # The ids come back from the database so they carry whatever form this
    # dialect stored them in.
    report = BackfillReport()
    for user_id, company_id in db.session.execute(_COMPANY_ACCESS_SQL, {"cid": id_param(company.id)}):
        ensure_directory_profile(db.session.connection(), user_id, company_id, report)

    db.session.commit()
    return {
        "company_id": company.id,
        "project_id": project.id,
        **{f"{role}_user_id": user.id for role, user in users.items()},
    }


if __name__ == "__main__":
    from scripts.qa_personas_cli import main

    raise SystemExit(main())
