"""Seed script for project assignments (user_projects entries).

Assigns users to projects beyond the project owner, so test scenarios have
realistic multi-member projects (membership-gated endpoints, project-scoped
note visibility, bulk-add edge cases). An assignment carries no role — what
the person may do comes from their company role.

Idempotent: re-running skips memberships whose (user_id, project_id) already
exist in the user_projects association.

Standalone usage:
    EMAIL_PROVIDER=inmemory uv run python -m scripts.seed_memberships

Typically invoked via the main `scripts/seed.py` orchestrator with
`--with-memberships` (requires --with-admin + --with-projects + --with-users).
"""

from __future__ import annotations

from datetime import datetime, timezone

from app import db
from app.infrastructure.database.models import (
    ProjectModel,
    UserModel,
    user_projects,
)

# Assignment plan: project name → the emails assigned to it. The project owner
# is implicit via ProjectModel.owner_id and is NOT listed here.
MEMBERSHIP_PLAN: dict[str, list[str]] = {
    "Downtown Office Tower": [
        "manager.alice@example.com",
        "user.dave@example.com",
        "user.eve@example.com",
    ],
    "Riverside Apartments": [
        "manager.bob@example.com",
        "user.frank@example.com",
        "user.grace@example.com",
        "user.dave@example.com",  # cross-project member for multi-project tests
    ],
    "Shopping Mall Renovation": [
        "manager.carol@example.com",
        "user.henry@example.com",
    ],
}


def seed_memberships(
    user_map: dict[str, UserModel],
    invited_by: UserModel | None,
) -> int:
    """Create user_projects entries per the MEMBERSHIP_PLAN.

    Args:
        user_map: email → UserModel from seed_test_users()
        invited_by: the user to stamp as `invited_by_user_id` (typically admin)

    Returns:
        Count of assignments actually created (skipped duplicates not counted).
    """
    created = 0
    now = datetime.now(timezone.utc)

    for project_name, members in MEMBERSHIP_PLAN.items():
        project = db.session.query(ProjectModel).filter_by(name=project_name).first()
        if not project:
            print(f"  [warn] Project '{project_name}' not found; skipping its memberships")
            continue

        for email in members:
            user = user_map.get(email.lower()) or db.session.query(UserModel).filter_by(email=email.lower()).first()
            if not user:
                print(f"    [warn] User '{email}' not found; skipping")
                continue

            # Don't duplicate-member the owner (would violate composite PK)
            if user.id == project.owner_id:
                print(f"    [skip] {email} is the project owner; not re-adding as member")
                continue

            # Check existing membership via raw SQL on the association table
            existing = db.session.execute(
                user_projects.select().where(
                    (user_projects.c.user_id == user.id) & (user_projects.c.project_id == project.id)
                )
            ).first()
            if existing:
                print(f"    [skip] {email} already member of '{project_name}'")
                continue

            db.session.execute(
                user_projects.insert().values(
                    user_id=user.id,
                    project_id=project.id,
                    invited_by_user_id=invited_by.id if invited_by else None,
                    assigned_at=now,
                )
            )
            created += 1
            print(f"    [add]  {email} → '{project_name}'")

    db.session.commit()
    print(f"\n  Created {created} assignments across {len(MEMBERSHIP_PLAN)} project(s).")
    return created


def _first_company_admin() -> UserModel | None:
    """Return any user attached to a company as `admin`, or None."""
    from app.infrastructure.database.models import UserCompanyAccessModel

    return (
        db.session.query(UserModel)
        .join(UserCompanyAccessModel, UserCompanyAccessModel.user_id == UserModel.id)
        .filter(UserCompanyAccessModel.role == "admin")
        .first()
    )


def main() -> None:
    """Standalone entry point — assumes admin, projects and users already seeded."""
    from app import create_app
    from scripts.seed_users import seed_test_users

    app = create_app()
    with app.app_context():
        print("Seeding assignments...")
        user_map = seed_test_users()

        # Stamp the invitation on any company admin of the seeded company.
        admin = _first_company_admin()
        seed_memberships(user_map, admin)
        print("\n  Done.")


if __name__ == "__main__":
    main()
