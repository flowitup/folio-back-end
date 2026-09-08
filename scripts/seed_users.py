"""Seed script for a richer test user roster.

Creates the roster used by every other seed script, all with a hardcoded
password (`password123`) so any test scenario has the credentials it needs.
What each of them may do comes from the company role assigned in
`scripts/seed_companies.py`, not from anything stored here.

Idempotent: re-running skips users that already exist (matched by email).

`--with-ops` additionally flags `superadmin@example.com` as platform ops, the
only way to reach `/api/v1/admin/*` on a freshly seeded database (the flag is
deliberately off by default: it is not a tenant role).

Standalone usage:
    EMAIL_PROVIDER=inmemory uv run python -m scripts.seed_users [--with-ops]

Typically invoked via the main `scripts/seed.py` orchestrator with
`--with-users` (requires --with-admin first).
"""

from __future__ import annotations

import os
import sys
from uuid import uuid4

from argon2 import PasswordHasher

from app import db
from app.infrastructure.database.models import UserModel

# Hardcoded password for ALL seeded test users — DEV/TEST ONLY.
TEST_PASSWORD = "password123"

# The roster member `--with-ops` flags as platform ops.
OPS_EMAIL = "superadmin@example.com"

# Roster: (email, display_name | None, is_active). The company role each of
# them gets is in scripts/seed_companies._COMPANY_ROLE_FOR_EMAIL.
TEST_USERS: list[tuple[str, str | None, bool]] = [
    ("superadmin@example.com", "Super Admin", True),
    ("admin2@example.com", "Second Admin", True),
    ("manager.alice@example.com", "Alice Manager", True),
    ("manager.bob@example.com", "Bob Manager", True),
    ("manager.carol@example.com", None, True),  # no display_name → falls back to email
    ("user.dave@example.com", "Dave User", True),
    ("user.eve@example.com", "Eve User", True),
    ("user.frank@example.com", None, True),
    ("user.grace@example.com", "Grace User", True),
    ("user.henry@example.com", None, True),
    # Inactive user — exists but cannot log in; covers inactive-account paths
    ("inactive@example.com", "Inactive User", False),
]


# Dev phone numbers for SMS-code sign-in (SMS_PROVIDER=log prints the code in the API log).
TEST_PHONES: dict[str, str] = {
    "superadmin@example.com": "+33600000001",
    "admin2@example.com": "+33600000002",
    "manager.alice@example.com": "+33600000003",
    "manager.bob@example.com": "+33600000004",
    "user.dave@example.com": "+84900000005",
}


def seed_test_users(with_ops: bool = False) -> dict[str, UserModel]:
    """Create the test user roster. Returns dict of email → UserModel.

    With `with_ops`, `superadmin@example.com` also gets the platform-ops flag
    so the `/api/v1/admin/*` endpoints are reachable on a dev database.
    """
    # Hardcoded TEST_PASSWORD must never reach production.
    if os.environ.get("FLASK_ENV") == "production":
        raise RuntimeError("REFUSING to seed test users with hardcoded password in FLASK_ENV=production.")
    ph = PasswordHasher()
    password_hash = ph.hash(TEST_PASSWORD)
    created_count = 0
    user_map: dict[str, UserModel] = {}

    for email, display_name, is_active in TEST_USERS:
        existing = db.session.query(UserModel).filter_by(email=email.lower()).first()
        if existing:
            user_map[email.lower()] = existing
            if email.lower() in TEST_PHONES and existing.phone != TEST_PHONES[email.lower()]:
                existing.phone = TEST_PHONES[email.lower()]
                print(f"  [phone] {email} → {existing.phone}")
            else:
                print(f"  [skip] {email} already exists")
            continue

        user = UserModel(
            id=uuid4(),
            email=email.lower(),
            password_hash=password_hash,
            is_active=is_active,
            display_name=display_name,
            phone=TEST_PHONES.get(email.lower()),
        )
        db.session.add(user)
        user_map[email.lower()] = user
        created_count += 1
        active_marker = "" if is_active else " (inactive)"
        display_marker = f" '{display_name}'" if display_name else ""
        print(f"  [add]  {email}{display_marker}{active_marker}")

    if with_ops:
        ops_user = user_map.get(OPS_EMAIL)
        if ops_user is None:
            print(f"  [ops]  {OPS_EMAIL} not in the roster — skipped")
        elif not ops_user.is_platform_ops:
            ops_user.is_platform_ops = True
            print(f"  [ops]  {OPS_EMAIL} is now platform ops")

    db.session.commit()
    print(f"\n  Created {created_count} test users (password: '{TEST_PASSWORD}')")
    return user_map


def main() -> None:
    """Standalone entry point."""
    from app import create_app

    app = create_app()
    with app.app_context():
        print("Seeding test users...")
        seed_test_users(with_ops="--with-ops" in sys.argv)
        print("\n  Done.")


if __name__ == "__main__":
    main()
