"""Create or remove the three QA personas (admin / manager / member) of a QA company.

Every persona set is tagged by a suffix: the company is named
``Folio QA <suffix>`` and nothing outside that company is ever touched, so
several QA runs can coexist and `--delete` can never reach production data.

    uv run python -m scripts.qa_personas --create --suffix smoke \\
        --admin-email qa.admin@example.com \\
        --manager-email qa.manager@example.com \\
        --member-email qa.member@example.com \\
        --password 'password123'

    uv run python -m scripts.qa_personas --delete --suffix smoke \\
        --admin-email ... --manager-email ... --member-email ...

`--create` is idempotent: re-running finds the company, the users, the
attachments, the project and the assignments it made last time. `--delete`
removes them and everything QA produced inside that company in a single
transaction, and prints one line per table.
"""

from __future__ import annotations

import argparse
import sys
from uuid import UUID

from sqlalchemy import text

from app.infrastructure.database.backfills.authz_backfill_report import BackfillReport
from app.infrastructure.database.backfills.directory_profiles import ensure_directory_profile
from scripts.qa_personas_purge import purge_company

COMPANY_PREFIX = "Folio QA "
DEFAULT_ADDRESS = "1 rue de la Recette, 75000 Paris"


def _same_id(left, right) -> bool:
    """Compare ids across insert paths (SQLite keeps UUIDs as dashless hex)."""
    return str(left).replace("-", "").lower() == str(right).replace("-", "").lower()


def _company_name(suffix: str) -> str:
    return f"{COMPANY_PREFIX}{suffix.strip()}"


def _project_name(suffix: str) -> str:
    return f"Folio QA Project {suffix.strip()}"


# ---------------------------------------------------------------------------
# create
# ---------------------------------------------------------------------------


def _ensure_user(email: str, password_hash: str, display_name: str):
    from app import db
    from app.infrastructure.database.models import UserModel

    user = db.session.query(UserModel).filter_by(email=email.lower()).first()
    if user is not None:
        user.password_hash = password_hash
        user.is_active = True
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
    from datetime import datetime, timezone

    from app import db
    from app.infrastructure.database.models import CompanyModel

    name = _company_name(suffix)
    company = db.session.query(CompanyModel).filter_by(legal_name=name).first()
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
    from datetime import datetime, timezone

    from app import db
    from app.infrastructure.database.models import UserCompanyAccessModel

    access = db.session.get(UserCompanyAccessModel, (user.id, company.id))
    if access is not None:
        access.role = role
        return
    db.session.add(
        UserCompanyAccessModel(
            user_id=user.id,
            company_id=company.id,
            role=role,
            is_primary=True,
            attached_at=datetime.now(timezone.utc),
        )
    )


def _ensure_project(suffix: str, company, owner):
    from app import db
    from app.infrastructure.database.models import ProjectModel

    name = _project_name(suffix)
    project = db.session.query(ProjectModel).filter_by(name=name, company_id=company.id).first()
    if project is not None:
        return project
    project = ProjectModel(name=name, address=DEFAULT_ADDRESS, owner_id=owner.id, company_id=company.id)
    db.session.add(project)
    db.session.flush()
    return project


def _ensure_assignment(project, user) -> None:
    from datetime import datetime, timezone

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

    Returns the ids worth pasting into a QA session.
    """
    from app import db
    from app.infrastructure.adapters.argon2_hasher import Argon2PasswordHasher

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
    for user_id, company_id in db.session.execute(text("SELECT user_id, company_id FROM user_company_access")):
        if _same_id(company_id, company.id):
            ensure_directory_profile(db.session.connection(), user_id, company_id, report)

    db.session.commit()
    return {
        "company_id": company.id,
        "project_id": project.id,
        **{f"{role}_user_id": user.id for role, user in users.items()},
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--create", action="store_true", help="create (or refresh) the personas")
    mode.add_argument("--delete", action="store_true", help="delete everything tagged with the suffix")
    parser.add_argument("--suffix", required=True, help="tag for this persona set, e.g. 'smoke'")
    parser.add_argument("--admin-email", required=True)
    parser.add_argument("--manager-email", required=True)
    parser.add_argument("--member-email", required=True)
    parser.add_argument("--password", help="password for all three personas (required with --create)")
    parser.add_argument("--address", default=DEFAULT_ADDRESS, help="company address")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    from app import create_app

    args = _parse_args(argv if argv is not None else sys.argv[1:])
    if not args.suffix.strip():
        print("--suffix must not be blank", file=sys.stderr)
        return 2
    if args.create and not args.password:
        print("--create requires --password", file=sys.stderr)
        return 2

    emails = {"admin": args.admin_email, "manager": args.manager_email, "member": args.member_email}

    app = create_app()
    with app.app_context():
        if args.create:
            ids = create_personas(args.suffix, emails, args.password, args.address)
            print(f"QA personas ready for '{_company_name(args.suffix)}':")
            for key, value in ids.items():
                print(f"  {key} = {value}")
            for role, email in emails.items():
                print(f"  {role}: {email}")
            return 0

        try:
            counts = purge_company(_company_name(args.suffix), list(emails.values()))
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        print(f"Deleted QA data for '{_company_name(args.suffix)}':")
        for table, count in counts.items():
            print(f"  {table}: {count}")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
