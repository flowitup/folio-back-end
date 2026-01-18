"""
Seed script for authentication data.

Creates default roles, permissions, and optionally an admin user.

Usage:
    # Using CLI args (visible in shell history - dev only)
    uv run python scripts/seed_auth.py --with-admin admin@example.com password

    # Using environment variables (recommended for production)
    ADMIN_EMAIL=admin@example.com ADMIN_PASSWORD=secret uv run python scripts/seed_auth.py --with-admin

Security Note:
    Prefer environment variables over CLI args to avoid password exposure
    in shell history. CLI args are acceptable for local development only.
"""

import os
import sys
from argon2 import PasswordHasher
from uuid import uuid4

from app import create_app, db
from app.infrastructure.database.models import (
    PermissionModel,
    RoleModel,
    UserModel,
)


# Default permissions: resource:action format
DEFAULT_PERMISSIONS = [
    # Admin wildcard
    {"name": "*:*", "resource": "*", "action": "*"},
    # Project permissions
    {"name": "project:create", "resource": "project", "action": "create"},
    {"name": "project:read", "resource": "project", "action": "read"},
    {"name": "project:update", "resource": "project", "action": "update"},
    {"name": "project:delete", "resource": "project", "action": "delete"},
    # User permissions
    {"name": "user:create", "resource": "user", "action": "create"},
    {"name": "user:read", "resource": "user", "action": "read"},
    {"name": "user:update", "resource": "user", "action": "update"},
    {"name": "user:delete", "resource": "user", "action": "delete"},
]

# Default roles with their permissions
DEFAULT_ROLES = [
    {
        "name": "admin",
        "description": "Full system access - all resources and actions",
        "permissions": ["*:*"],
    },
    {
        "name": "manager",
        "description": "Project management access",
        "permissions": [
            "project:create",
            "project:read",
            "project:update",
            "project:delete",
            "user:read",
        ],
    },
    {
        "name": "user",
        "description": "Basic user access",
        "permissions": ["project:read", "user:read"],
    },
]


def seed_permissions() -> dict:
    """Create default permissions. Returns dict of name -> PermissionModel."""
    permission_map = {}

    for perm_data in DEFAULT_PERMISSIONS:
        existing = db.session.query(PermissionModel).filter_by(name=perm_data["name"]).first()
        if existing:
            print(f"  Permission '{perm_data['name']}' already exists, skipping.")
            permission_map[perm_data["name"]] = existing
        else:
            perm = PermissionModel(
                id=uuid4(),
                name=perm_data["name"],
                resource=perm_data["resource"],
                action=perm_data["action"],
            )
            db.session.add(perm)
            permission_map[perm_data["name"]] = perm
            print(f"  Created permission: {perm_data['name']}")

    db.session.commit()
    return permission_map


def seed_roles(permission_map: dict) -> dict:
    """Create default roles with permissions. Returns dict of name -> RoleModel."""
    role_map = {}

    for role_data in DEFAULT_ROLES:
        existing = db.session.query(RoleModel).filter_by(name=role_data["name"]).first()
        if existing:
            print(f"  Role '{role_data['name']}' already exists, skipping.")
            role_map[role_data["name"]] = existing
        else:
            role = RoleModel(
                id=uuid4(),
                name=role_data["name"],
                description=role_data["description"],
            )
            # Assign permissions
            for perm_name in role_data["permissions"]:
                if perm_name in permission_map:
                    role.permissions.append(permission_map[perm_name])
            db.session.add(role)
            role_map[role_data["name"]] = role
            print(f"  Created role: {role_data['name']} with {len(role_data['permissions'])} permissions")

    db.session.commit()
    return role_map


def create_admin_user(email: str, password: str, role_map: dict) -> None:
    """Create an admin user with hashed password."""
    existing = db.session.query(UserModel).filter_by(email=email.lower()).first()
    if existing:
        print(f"  User '{email}' already exists, skipping.")
        return

    ph = PasswordHasher()
    password_hash = ph.hash(password)

    user = UserModel(
        id=uuid4(),
        email=email.lower(),
        password_hash=password_hash,
        is_active=True,
    )

    # Assign admin role
    if "admin" in role_map:
        user.roles.append(role_map["admin"])

    db.session.add(user)
    db.session.commit()
    print(f"  Created admin user: {email}")


def main():
    """Main entry point for seeding."""
    app = create_app()

    with app.app_context():
        print("Seeding authentication data...")

        print("\n1. Creating permissions...")
        permission_map = seed_permissions()

        print("\n2. Creating roles...")
        role_map = seed_roles(permission_map)

        # Check for admin user creation
        if "--with-admin" in sys.argv:
            # Try environment variables first (recommended)
            email = os.environ.get("ADMIN_EMAIL")
            password = os.environ.get("ADMIN_PASSWORD")

            # Fall back to CLI args if env vars not set
            if not email or not password:
                idx = sys.argv.index("--with-admin")
                if idx + 2 < len(sys.argv):
                    email = sys.argv[idx + 1]
                    password = sys.argv[idx + 2]
                    print("\n  Warning: Using CLI args for credentials (visible in shell history)")
                    print("  Consider using ADMIN_EMAIL and ADMIN_PASSWORD env vars instead.")

            if email and password:
                print(f"\n3. Creating admin user: {email}...")
                create_admin_user(email, password, role_map)
            else:
                print("\nError: --with-admin requires credentials")
                print("Options:")
                print("  1. Environment vars: ADMIN_EMAIL=x ADMIN_PASSWORD=y python scripts/seed_auth.py --with-admin")
                print("  2. CLI args (dev only): python scripts/seed_auth.py --with-admin email password")
                sys.exit(1)

        print("\nSeeding complete!")


if __name__ == "__main__":
    main()
