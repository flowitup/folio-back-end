"""Seed script for a richer test user roster.

Creates a mix of admins / managers / regular users / one inactive user, all with
a hardcoded password (`password123`) so any test scenario has the credentials it
needs.

Idempotent: re-running skips users that already exist (matched by email).

Standalone usage:
    EMAIL_PROVIDER=inmemory uv run python -m scripts.seed_users

Typically invoked via the main `scripts/seed.py` orchestrator with `--with-users`
(requires --with-admin first so roles are seeded).
"""

from __future__ import annotations

from uuid import uuid4

from argon2 import PasswordHasher

from app import db
from app.infrastructure.database.models import RoleModel, UserModel

# Hardcoded password for ALL seeded test users — DEV/TEST ONLY.
TEST_PASSWORD = "password123"

# Roster: (email, role_name, display_name | None, is_active)
TEST_USERS: list[tuple[str, str, str | None, bool]] = [
    # Admins (2) — full *:* permission via the existing 'admin' role
    ("superadmin@example.com", "admin", "Super Admin", True),
    ("admin2@example.com", "admin", "Second Admin", True),
    # Managers (3) — project management permissions
    ("manager.alice@example.com", "manager", "Alice Manager", True),
    ("manager.bob@example.com", "manager", "Bob Manager", True),
    ("manager.carol@example.com", "manager", None, True),  # no display_name → falls back to email
    # Regular users (5) — basic read access
    ("user.dave@example.com", "user", "Dave User", True),
    ("user.eve@example.com", "user", "Eve User", True),
    ("user.frank@example.com", "user", None, True),
    ("user.grace@example.com", "user", "Grace User", True),
    ("user.henry@example.com", "user", None, True),
    # Inactive user (1) — exists but cannot log in; covers inactive-account paths
    ("inactive@example.com", "user", "Inactive User", False),
]


def seed_test_users(role_map: dict[str, RoleModel]) -> dict[str, UserModel]:
    """Create the test user roster. Returns dict of email → UserModel.

    Args:
        role_map: dict from seed_auth.seed_roles() — name → RoleModel.
    """
    ph = PasswordHasher()
    password_hash = ph.hash(TEST_PASSWORD)
    created_count = 0
    user_map: dict[str, UserModel] = {}

    for email, role_name, display_name, is_active in TEST_USERS:
        existing = db.session.query(UserModel).filter_by(email=email.lower()).first()
        if existing:
            user_map[email.lower()] = existing
            print(f"  [skip] {email} already exists")
            continue

        if role_name not in role_map:
            print(f"  [warn] role '{role_name}' not in role_map; skipping {email}")
            continue

        user = UserModel(
            id=uuid4(),
            email=email.lower(),
            password_hash=password_hash,
            is_active=is_active,
            display_name=display_name,
        )
        user.roles.append(role_map[role_name])
        db.session.add(user)
        user_map[email.lower()] = user
        created_count += 1
        active_marker = "" if is_active else " (inactive)"
        display_marker = f" '{display_name}'" if display_name else ""
        print(f"  [add]  {email} ({role_name}){display_marker}{active_marker}")

    db.session.commit()
    print(f"\n  Created {created_count} test users (password: '{TEST_PASSWORD}')")
    return user_map


def main() -> None:
    """Standalone entry point — assumes permissions/roles already seeded."""
    from app import create_app
    from scripts.seed_auth import seed_permissions, seed_roles

    app = create_app()
    with app.app_context():
        print("Seeding test users...")
        permission_map = seed_permissions()
        role_map = seed_roles(permission_map)
        seed_test_users(role_map)
        print("\n  Done.")


if __name__ == "__main__":
    main()
