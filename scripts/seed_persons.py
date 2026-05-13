"""Seed script for Person identity entities.

Creates a small sample of Person rows owned by the admin user. Each
Person is the global-identity counterpart to a Worker (Phase 1b-ii);
the Phase 1c backfill script will link existing per-project Workers
to the matching Person row by phone/normalized_name.

Idempotent: re-running skips persons whose normalized_name+phone is
already present (matches the dedup signature the merge tool will use).
"""

from datetime import datetime, timezone
from uuid import uuid4

from app import db
from app.domain.entities.person import Person
from app.infrastructure.database.models import PersonModel, UserModel

# Sample roster — mirrors the worker names in seed_labor.py so the future
# backfill script can demonstrate the link step cleanly. Phone numbers
# match seed_labor.py exactly to exercise phone-based dedup.
DEFAULT_PERSONS = [
    {"name": "Jean Dupont", "phone": "+33612345678"},
    {"name": "Pierre Martin", "phone": "+33623456789"},
    {"name": "Marie Bernard", "phone": "+33634567890"},
    {"name": "Lucas Dubois", "phone": "+33645678901"},
    {"name": "Sophie Moreau", "phone": "+33656789012"},
    # A couple of extras without phone — exercise the nullable phone path.
    {"name": "Hugo Martin", "phone": None},
    {"name": "Léo Dupont", "phone": None},
]


def seed_persons() -> None:
    """Seed Person rows. Owned by the first admin user found."""
    creator = db.session.query(UserModel).order_by(UserModel.created_at.asc()).first()
    if not creator:
        print("  No users found. Run with --with-admin first.")
        return

    now = datetime.now(timezone.utc)
    created = 0
    skipped = 0

    for data in DEFAULT_PERSONS:
        normalized = Person.normalize(data["name"])

        # Dedup signature: same normalized_name AND same phone (NULL counts
        # as matching NULL). Mirrors the merge-tool heuristic.
        existing = (
            db.session.query(PersonModel)
            .filter(PersonModel.normalized_name == normalized)
            .filter(PersonModel.phone == data["phone"])
            .first()
        )
        if existing:
            print(f"    Person '{data['name']}' already exists, skipping.")
            skipped += 1
            continue

        person = PersonModel(
            id=uuid4(),
            name=data["name"],
            phone=data["phone"],
            normalized_name=normalized,
            created_by_user_id=creator.id,
            created_at=now,
            updated_at=now,
        )
        db.session.add(person)
        created += 1
        print(f"    Created person: {data['name']} ({data['phone'] or 'no phone'})")

    db.session.commit()
    print(f"  Seeded {created} persons ({skipped} already present).")
