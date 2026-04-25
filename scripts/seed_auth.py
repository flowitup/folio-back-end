"""Seed script for authentication data (permissions, roles, users)."""

import os
import sys
from argon2 import PasswordHasher
from uuid import uuid4

from app import db
from app.infrastructure.database.models import PermissionModel, RoleModel, UserModel


DEFAULT_PERMISSIONS = [
    {"name": "*:*", "resource": "*", "action": "*"},
    {"name": "project:create", "resource": "project", "action": "create"},
    {"name": "project:read", "resource": "project", "action": "read"},
    {"name": "project:update", "resource": "project", "action": "update"},
    {"name": "project:delete", "resource": "project", "action": "delete"},
    {"name": "project:manage_users", "resource": "project", "action": "manage_users"},
    {"name": "project:manage_labor", "resource": "project", "action": "manage_labor"},
    {"name": "project:manage_invoices", "resource": "project", "action": "manage_invoices"},
    {"name": "user:create", "resource": "user", "action": "create"},
    {"name": "user:read", "resource": "user", "action": "read"},
    {"name": "user:update", "resource": "user", "action": "update"},
    {"name": "user:delete", "resource": "user", "action": "delete"},
]

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
            "project:manage_users",
            "project:manage_labor",
            "project:manage_invoices",
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
            for perm_name in role_data["permissions"]:
                if perm_name in permission_map:
                    role.permissions.append(permission_map[perm_name])
            db.session.add(role)
            role_map[role_data["name"]] = role
            print(f"  Created role: {role_data['name']} with {len(role_data['permissions'])} permissions")

    db.session.commit()
    return role_map


def create_admin_user(email: str, password: str, role_map: dict) -> UserModel | None:
    """Create an admin user with hashed password.

    If the user already exists, reset their password and ensure the admin role is set.
    """
    ph = PasswordHasher()
    password_hash = ph.hash(password)

    existing = db.session.query(UserModel).filter_by(email=email.lower()).first()
    if existing:
        existing.password_hash = password_hash
        existing.is_active = True
        if "admin" in role_map and role_map["admin"] not in existing.roles:
            existing.roles.append(role_map["admin"])
        db.session.commit()
        print(f"  User '{email}' already existed — password reset and admin role ensured.")
        return existing

    user = UserModel(
        id=uuid4(),
        email=email.lower(),
        password_hash=password_hash,
        is_active=True,
    )

    if "admin" in role_map:
        user.roles.append(role_map["admin"])

    db.session.add(user)
    db.session.commit()
    print(f"  Created admin user: {email}")
    return user


def create_client_user(email: str, password: str, role_map: dict) -> UserModel | None:
    """Create a client user with basic 'user' role."""
    existing = db.session.query(UserModel).filter_by(email=email.lower()).first()
    if existing:
        print(f"  User '{email}' already exists, skipping.")
        return existing

    ph = PasswordHasher()
    password_hash = ph.hash(password)

    user = UserModel(
        id=uuid4(),
        email=email.lower(),
        password_hash=password_hash,
        is_active=True,
    )

    if "user" in role_map:
        user.roles.append(role_map["user"])

    db.session.add(user)
    db.session.commit()
    print(f"  Created client user: {email}")
    return user


def get_admin_credentials() -> tuple[str | None, str | None]:
    """Get admin credentials from env vars or CLI args."""
    email = os.environ.get("ADMIN_EMAIL")
    password = os.environ.get("ADMIN_PASSWORD")

    if not email or not password:
        if "--with-admin" in sys.argv:
            idx = sys.argv.index("--with-admin")
            if idx + 2 < len(sys.argv):
                email = sys.argv[idx + 1]
                password = sys.argv[idx + 2]
                print("\n  Warning: Using CLI args for credentials (visible in shell history)")
                print("  Consider using ADMIN_EMAIL and ADMIN_PASSWORD env vars instead.")

    return email, password
