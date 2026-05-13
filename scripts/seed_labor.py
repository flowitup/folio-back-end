"""Seed script for labor data (workers and labor entries)."""

from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4

from app import db
from app.infrastructure.database.models import ProjectModel, WorkerModel, LaborEntryModel

DEFAULT_WORKERS = [
    {"name": "Jean Dupont", "phone": "+33612345678", "daily_rate": Decimal("150.00")},
    {"name": "Pierre Martin", "phone": "+33623456789", "daily_rate": Decimal("175.00")},
    {"name": "Marie Bernard", "phone": "+33634567890", "daily_rate": Decimal("140.00")},
    {"name": "Lucas Dubois", "phone": "+33645678901", "daily_rate": Decimal("200.00")},
    {"name": "Sophie Moreau", "phone": "+33656789012", "daily_rate": Decimal("165.00")},
]


def seed_workers(project: ProjectModel) -> list[WorkerModel]:
    """Create sample workers for a project. Returns list of created workers."""
    workers = []

    for worker_data in DEFAULT_WORKERS:
        existing = db.session.query(WorkerModel).filter_by(project_id=project.id, name=worker_data["name"]).first()
        if existing:
            print(f"    Worker '{worker_data['name']}' already exists, skipping.")
            workers.append(existing)
            continue

        worker = WorkerModel(
            id=uuid4(),
            project_id=project.id,
            name=worker_data["name"],
            phone=worker_data["phone"],
            daily_rate=worker_data["daily_rate"],
            is_active=True,
        )
        db.session.add(worker)
        workers.append(worker)
        print(f"    Created worker: {worker_data['name']} (rate: {worker_data['daily_rate']})")

    db.session.commit()
    return workers


def seed_labor_entries(workers: list[WorkerModel]) -> None:
    """Create sample labor entries for the past 14 days."""
    today = date.today()
    notes = [
        "Regular shift",
        "Overtime work",
        "Foundation work",
        "Electrical wiring",
        "Plumbing installation",
        None,
    ]

    for worker in workers:
        # Create entries for random days in the past 14 days
        for days_ago in [1, 2, 3, 5, 7, 8, 10, 12, 14]:
            entry_date = today - timedelta(days=days_ago)

            existing = db.session.query(LaborEntryModel).filter_by(worker_id=worker.id, date=entry_date).first()
            if existing:
                continue

            # Some entries have amount override (overtime pay)
            amount_override = None
            if days_ago in [3, 7]:
                amount_override = worker.daily_rate * Decimal("1.5")

            # Mix shift types so the seed exercises the constraint variants.
            # Required: shift_type IS NOT NULL OR supplement_hours > 0.
            if days_ago in [2, 10]:
                shift_type = "half"
                supplement_hours = 0
            elif days_ago in [5, 12]:
                shift_type = "full"
                supplement_hours = 2  # full day + 2h overtime supplement
            else:
                shift_type = "full"
                supplement_hours = 0

            note = notes[days_ago % len(notes)]

            entry = LaborEntryModel(
                id=uuid4(),
                worker_id=worker.id,
                date=entry_date,
                amount_override=amount_override,
                note=note,
                shift_type=shift_type,
                supplement_hours=supplement_hours,
            )
            db.session.add(entry)

    db.session.commit()
    print(f"    Created labor entries for {len(workers)} workers")


def seed_labor() -> None:
    """Seed labor data for all existing projects."""
    projects = db.session.query(ProjectModel).all()

    if not projects:
        print("  No projects found. Run with --with-projects first.")
        return

    for project in projects:
        print(f"  Seeding labor for project: {project.name}")
        workers = seed_workers(project)
        seed_labor_entries(workers)
