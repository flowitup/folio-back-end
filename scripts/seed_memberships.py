"""Seed script for project memberships (user_projects entries).

Adds users to projects beyond the project owner, so test scenarios have realistic
multi-member projects (for testing membership-gated endpoints, project-scoped
note visibility, bulk-add edge cases, etc.).

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
    RoleModel,
    UserModel,
    user_projects,
)

# Membership plan: project name → list of (email, role_name) tuples.
# The project owner is implicit via ProjectModel.owner_id and is NOT listed here.
MEMBERSHIP_PLAN: dict[str, list[tuple[str, str]]] = {
    "Downtown Office Tower": [
        ("manager.alice@example.com", "manager"),
        ("user.dave@example.com", "user"),
        ("user.eve@example.com", "user"),
    ],
    "Riverside Apartments": [
        ("manager.bob@example.com", "manager"),
        ("user.frank@example.com", "user"),
        ("user.grace@example.com", "user"),
        ("user.dave@example.com", "user"),  # cross-project member for multi-project tests
    ],
    "Shopping Mall Renovation": [
        ("manager.carol@example.com", "manager"),
        ("user.henry@example.com", "user"),
    ],
}


def seed_memberships(
    role_map: dict[str, RoleModel],
    user_map: dict[str, UserModel],
    invited_by: UserModel | None,
) -> int:
    """Create user_projects entries per the MEMBERSHIP_PLAN.

    Args:
        role_map: name → RoleModel from seed_roles()
        user_map: email → UserModel from seed_test_users()
        invited_by: the user to stamp as `invited_by_user_id` (typically admin)

    Returns:
        Count of memberships actually created (skipped duplicates not counted).
    """
    created = 0
    now = datetime.now(timezone.utc)

    for project_name, members in MEMBERSHIP_PLAN.items():
        project = db.session.query(ProjectModel).filter_by(name=project_name).first()
        if not project:
            print(f"  [warn] Project '{project_name}' not found; skipping its memberships")
            continue

        for email, role_name in members:
            user = user_map.get(email.lower()) or db.session.query(UserModel).filter_by(email=email.lower()).first()
            if not user:
                print(f"    [warn] User '{email}' not found; skipping")
                continue

            if role_name not in role_map:
                print(f"    [warn] Role '{role_name}' not in role_map; skipping {email}")
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
                    role_id=role_map[role_name].id,
                    invited_by_user_id=invited_by.id if invited_by else None,
                    assigned_at=now,
                )
            )
            created += 1
            print(f"    [add]  {email} → '{project_name}' as {role_name}")

    db.session.commit()
    print(f"\n  Created {created} memberships across {len(MEMBERSHIP_PLAN)} project(s).")
    return created


def main() -> None:
    """Standalone entry point — assumes admin, projects, users, roles already seeded."""
    from app import create_app
    from scripts.seed_auth import seed_permissions, seed_roles
    from scripts.seed_users import seed_test_users

    app = create_app()
    with app.app_context():
        print("Seeding memberships...")
        permission_map = seed_permissions()
        role_map = seed_roles(permission_map)
        user_map = seed_test_users(role_map)

        # Pick the project owner / admin as `invited_by`
        admin = db.session.query(UserModel).join(UserModel.roles).filter(RoleModel.name == "admin").first()
        seed_memberships(role_map, user_map, admin)
        print("\n  Done.")


if __name__ == "__main__":
    main()
