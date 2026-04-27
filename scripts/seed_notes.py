"""Seed script for project notes + per-user dismissals.

Creates a realistic spread of notes across every agenda bucket the FE renders:

    Today      — due_date=today,           lead_time=0     (firing now)
    Tomorrow   — due_date=today+1,         lead_time=60    (1h-before reminder fires today 09:00 UTC)
    This week  — due_date=today+3 / +5,    lead_time=1440  (1d-before reminder fires within the week)
    Later      — due_date=today+30,        lead_time=0     (not yet firing)
    Done       — status=done                                (completed; collapses in agenda)
    Overdue    — due_date=today-1,         lead_time=0     (still firing, members haven't dismissed)

Plus a few `notes_dismissed` rows so the lazy-compute notification endpoint has
something to filter out for testing dismissed-vs-undismissed scenarios.

Idempotent: re-running skips notes whose (project_id, title) already exist.

Standalone usage:
    EMAIL_PROVIDER=inmemory uv run python -m scripts.seed_notes

Typically invoked via the main `scripts/seed.py` orchestrator with
`--with-notes` (requires --with-admin + --with-projects + --with-users +
--with-memberships).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from uuid import UUID, uuid4

from app import db
from app.infrastructure.database.models import (
    NoteDismissalOrm,
    NoteOrm,
    ProjectModel,
    UserModel,
)


def _today() -> date:
    return date.today()


# Note plan — applied per project (so each project gets all 6 buckets covered).
# Tuple: (title, description | None, days_offset_from_today, lead_time_minutes, status, creator_email_override | None)
# `creator_email_override` lets a few notes be authored by managers/users instead of admin
# so the data covers the "any project member can create" case.
NOTE_PLAN: list[tuple[str, str | None, int, int, str, str | None]] = [
    # Today — firing right now
    ("Site safety inspection", "Walk the perimeter, check signage.", 0, 0, "open", None),
    ("Daily stand-up at 09:00", None, 0, 0, "open", "manager.alice@example.com"),
    # Tomorrow with 1h lead-time (fires today at 09:00 UTC)
    (
        "Material delivery acceptance",
        "Cement + rebar arriving — sign off with foreman.",
        1,
        60,
        "open",
        "manager.bob@example.com",
    ),
    # This week with 1-day lead-time (fires the day before)
    ("Subcontractor invoice review", "EuroBat invoice batch — verify hours.", 3, 1440, "open", None),
    ("Budget review meeting", None, 5, 1440, "open", "user.dave@example.com"),
    # Later (non-firing)
    ("Quarterly compliance audit", "External audit kickoff.", 30, 0, "open", None),
    # Done bucket
    ("Foundation pour — phase 1", "Completed; documented in invoice INV-2026-0001.", -2, 0, "done", None),
    ("Initial site survey", None, -7, 0, "done", "manager.carol@example.com"),
    # Overdue (still firing — members didn't dismiss yet)
    ("Permit renewal — DEADLINE", "Submit by city hall by EOD.", -1, 0, "open", None),
]


def _resolve_creator(email_override: str | None, fallback: UserModel, user_cache: dict[str, UserModel]) -> UUID:
    """Pick the creator user for a seeded note.

    If email_override is set and the user exists, use them; otherwise fall back to admin.
    Cached lookups by email to avoid N queries.
    """
    if not email_override:
        return fallback.id

    cached = user_cache.get(email_override.lower())
    if cached:
        return cached.id

    user = db.session.query(UserModel).filter_by(email=email_override.lower()).first()
    if user:
        user_cache[email_override.lower()] = user
        return user.id

    print(f"    [warn] creator '{email_override}' not found; falling back to admin")
    return fallback.id


def _seed_notes_for_project(
    project: ProjectModel,
    admin: UserModel,
    user_cache: dict[str, UserModel],
) -> list[NoteOrm]:
    """Seed all NOTE_PLAN entries for one project. Returns list of created notes (skips existing)."""
    today = _today()
    now = datetime.now(timezone.utc)
    created: list[NoteOrm] = []

    for title, description, days_offset, lead_time, status, creator_override in NOTE_PLAN:
        existing = db.session.query(NoteOrm).filter_by(project_id=project.id, title=title).first()
        if existing:
            print(f"    [skip] '{title}'")
            continue

        creator_id = _resolve_creator(creator_override, admin, user_cache)
        due = today + timedelta(days=days_offset)
        # Backdate created_at slightly for realism (older notes look older)
        created_at = now - timedelta(days=max(0, -days_offset) + 1) if days_offset < 0 else now

        note = NoteOrm(
            id=uuid4(),
            project_id=project.id,
            created_by=creator_id,
            title=title,
            description=description,
            due_date=due,
            lead_time_minutes=lead_time,
            status=status,
            created_at=created_at,
            updated_at=created_at,
        )
        db.session.add(note)
        created.append(note)
        print(f"    [add]  '{title}' (due={due}, lead={lead_time}min, status={status})")

    db.session.commit()
    return created


def _seed_dismissals(
    notes: list[NoteOrm],
    user_cache: dict[str, UserModel],
) -> int:
    """Seed a few notes_dismissed rows so the polling endpoint has data to filter.

    Strategy: for each project, dismiss the 'Today daily stand-up' note for one user
    + dismiss the 'Material delivery' note for another user. Keeps overdue + permit
    notes UNdismissed so they always show in the bell-icon dropdown.
    """
    if not notes:
        return 0

    # Pick two test users to dismiss for
    dismissers = [
        user_cache.get("user.dave@example.com")
        or db.session.query(UserModel).filter_by(email="user.dave@example.com").first(),
        user_cache.get("user.eve@example.com")
        or db.session.query(UserModel).filter_by(email="user.eve@example.com").first(),
    ]
    dismissers = [u for u in dismissers if u is not None]
    if not dismissers:
        print("    [warn] no test users available for dismissals; skipping")
        return 0

    # Titles to dismiss (subset of currently-firing notes)
    titles_to_dismiss = ["Daily stand-up at 09:00", "Material delivery acceptance"]
    now = datetime.now(timezone.utc)
    created = 0

    for note in notes:
        if note.title not in titles_to_dismiss:
            continue
        # Round-robin dismisser based on title
        dismisser = dismissers[hash(note.title) % len(dismissers)]

        existing = db.session.query(NoteDismissalOrm).filter_by(user_id=dismisser.id, note_id=note.id).first()
        if existing:
            print(f"    [skip] '{note.title}' already dismissed by {dismisser.email}")
            continue

        dismissal = NoteDismissalOrm(
            user_id=dismisser.id,
            note_id=note.id,
            dismissed_at=now - timedelta(hours=2),  # dismissed 2h ago
        )
        db.session.add(dismissal)
        created += 1
        print(f"    [add]  dismissal: '{note.title}' by {dismisser.email}")

    db.session.commit()
    return created


def seed_notes() -> None:
    """Seed notes + dismissals across all existing projects.

    Requires: at least one admin user + at least one project. Project members
    are queried lazily for creator overrides; if a referenced manager/user doesn't
    exist, falls back to the admin.
    """
    projects = db.session.query(ProjectModel).all()
    if not projects:
        print("  No projects found. Run seed.py with --with-admin --with-projects first.")
        return

    # Fall-back creator: any user (preferably one with the admin role)
    from app.infrastructure.database.models import RoleModel  # local import to avoid cycle

    admin = db.session.query(UserModel).join(UserModel.roles).filter(RoleModel.name == "admin").first()
    if not admin:
        admin = db.session.query(UserModel).first()
    if not admin:
        print("  No users found. Run seed.py with --with-admin first.")
        return

    user_cache: dict[str, UserModel] = {}
    total_notes = 0
    total_dismissals = 0

    for project in projects:
        print(f"\n  Project: {project.name}")
        notes = _seed_notes_for_project(project, admin, user_cache)
        total_notes += len(notes)
        total_dismissals += _seed_dismissals(notes, user_cache)

    print(f"\n  Done. Created {total_notes} notes + {total_dismissals} dismissals across {len(projects)} project(s).")


def main() -> None:
    """Standalone entry point — assumes admin + projects already seeded."""
    from app import create_app

    app = create_app()
    with app.app_context():
        print("Seeding notes...")
        seed_notes()


if __name__ == "__main__":
    main()
