"""Seed script for authentication data (users).

Permissions are derived in code (`app.domain.authz.matrix`) from the company
role, so there is nothing to seed for them: a user becomes an admin by being
attached to a company as `admin` (see scripts/seed_companies.py).
"""

import os
import sys
from argon2 import PasswordHasher
from uuid import uuid4

from app import db
from app.infrastructure.database.models import UserModel


def create_admin_user(email: str, password: str) -> UserModel | None:
    """Create the seed account with a hashed password.

    If the user already exists, reset their password and reactivate them.
    Company `admin` rights come from the attachment made in seed_companies.
    """
    ph = PasswordHasher()
    password_hash = ph.hash(password)

    existing = db.session.query(UserModel).filter_by(email=email.lower()).first()
    if existing:
        existing.password_hash = password_hash
        existing.is_active = True
        db.session.commit()
        print(f"  User '{email}' already existed — password reset.")
        return existing

    user = UserModel(
        id=uuid4(),
        email=email.lower(),
        password_hash=password_hash,
        is_active=True,
    )

    db.session.add(user)
    db.session.commit()
    print(f"  Created admin user: {email}")
    return user


def create_client_user(email: str, password: str) -> UserModel | None:
    """Create the legacy demo account; it joins the company as a `member`."""
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
