"""Seed script for authentication data (users).

Phone + SMS code is the only way to sign in — there is no password to seed
any more. Permissions are derived in code (`app.domain.authz.matrix`) from
the company role, so there is nothing to seed for them either: a user becomes
an admin by being attached to a company as `admin` (see
scripts/seed_companies.py).
"""

import os
import sys
from uuid import uuid4

from app import db
from app.infrastructure.database.models import UserModel
from app.domain.value_objects.phone_number import InvalidPhoneNumberError, normalize_french_phone


def create_admin_user(email: str, phone: str) -> UserModel | None:
    """Create the seed account with a real, sign-in-capable phone number.

    If the user already exists, update their phone and reactivate them.
    Company `admin` rights come from the attachment made in seed_companies.
    """
    normalized_phone = normalize_french_phone(phone)

    existing = db.session.query(UserModel).filter_by(email=email.lower()).first()
    if existing:
        existing.phone = normalized_phone
        existing.is_active = True
        db.session.commit()
        print(f"  User '{email}' already existed — phone set to {normalized_phone}.")
        return existing

    user = UserModel(
        id=uuid4(),
        email=email.lower(),
        phone=normalized_phone,
        is_active=True,
    )

    db.session.add(user)
    db.session.commit()
    print(f"  Created admin user: {email} ({normalized_phone})")
    return user


def create_client_user(email: str, phone: str) -> UserModel | None:
    """Create the legacy demo account; it joins the company as a `member`."""
    existing = db.session.query(UserModel).filter_by(email=email.lower()).first()
    if existing:
        print(f"  User '{email}' already exists, skipping.")
        return existing

    normalized_phone = normalize_french_phone(phone)

    user = UserModel(
        id=uuid4(),
        email=email.lower(),
        phone=normalized_phone,
        is_active=True,
    )

    db.session.add(user)
    db.session.commit()
    print(f"  Created client user: {email} ({normalized_phone})")
    return user


def get_admin_credentials() -> tuple[str | None, str | None]:
    """Get the admin's email + French phone number from env vars or CLI args."""
    email = os.environ.get("ADMIN_EMAIL")
    phone = os.environ.get("ADMIN_PHONE")

    if not email or not phone:
        if "--with-admin" in sys.argv:
            idx = sys.argv.index("--with-admin")
            if idx + 2 < len(sys.argv):
                email = sys.argv[idx + 1]
                phone = sys.argv[idx + 2]
                print("\n  Warning: Using CLI args for credentials (visible in shell history)")
                print("  Consider using ADMIN_EMAIL and ADMIN_PHONE env vars instead.")

    if email and phone:
        try:
            normalize_french_phone(phone)
        except InvalidPhoneNumberError as exc:
            print(f"\n  Error: ADMIN_PHONE {phone!r} is not usable — {exc}")
            return email, None

    return email, phone
