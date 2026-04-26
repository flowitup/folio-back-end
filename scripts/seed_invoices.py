"""Seed script for invoice test data.

Idempotent: re-running skips invoices whose (project_id, invoice_number) already exist.
With --reset, deletes all existing invoices first.

Usage (inside the api container):
    docker compose exec api python -m scripts.seed_invoices
    docker compose exec api python -m scripts.seed_invoices --reset
    docker compose exec api python -m scripts.seed_invoices --per-project 8

Standalone:
    uv run python -m scripts.seed_invoices [--reset] [--per-project N]
"""

from __future__ import annotations

import argparse
from datetime import date, timedelta
from uuid import uuid4

from app import create_app, db
from app.infrastructure.database.models import ProjectModel, UserModel
from app.infrastructure.database.models.invoice import InvoiceModel

# Realistic mix of recipients per invoice type
RECIPIENTS = {
    "client": [
        ("Acme Holdings SARL", "12 Rue de la Paix\n75002 Paris"),
        ("BlueSky Investments", "8 Av. Victor Hugo\n69006 Lyon"),
        ("Coastal Properties Ltd", "23 Promenade des Anglais\n06000 Nice"),
    ],
    "labor": [
        ("Jean Dupont", "Subcontractor — daily rate"),
        ("Atelier Martin Frères", "Subcontractor — masonry"),
        ("EuroBat Workforce", "Agency — rotating crew"),
    ],
    "supplier": [
        ("BTP Matériaux SA", "ZA Les Pinsons\n13100 Aix-en-Provence"),
        ("Lafarge Cement Distrib.", "Route Nationale 7\n38000 Grenoble"),
        ("ElectroPro Wholesale", "12 Rue du Cuivre\n92110 Clichy"),
    ],
}

# Line-item templates per invoice type (description, quantity, unit_price)
ITEM_TEMPLATES = {
    "client": [
        [
            ("Foundation works — phase 1", 1, 18500.00),
            ("Site preparation & excavation", 1, 4200.00),
        ],
        [
            ("Structural steel installation", 12, 1450.00),
            ("Crane rental (3 days)", 3, 850.00),
        ],
        [
            ("Project management fee — Q1", 1, 6800.00),
            ("Engineering consulting", 24, 125.00),
        ],
        [
            ("Roofing & waterproofing", 1, 9750.00),
            ("Insulation supply & install", 180, 22.50),
        ],
    ],
    "labor": [
        [
            ("Carpentry — 5 days × 2 workers", 10, 175.00),
            ("Overtime weekend shift", 8, 65.00),
        ],
        [
            ("Masonry team — week 12", 25, 165.00),
        ],
        [
            ("Electrical wiring crew", 15, 195.00),
            ("Apprentice support", 15, 95.00),
        ],
    ],
    "supplier": [
        [
            ("Cement bags 25kg", 120, 8.40),
            ("Rebar steel rods 12mm × 6m", 80, 14.20),
            ("Sand (m³)", 15, 38.00),
        ],
        [
            ("Electrical cable 3×2.5mm² (m)", 500, 2.85),
            ("Junction boxes IP65", 40, 12.90),
            ("Circuit breakers 16A", 20, 18.50),
        ],
        [
            ("Plywood panels 18mm", 60, 42.00),
            ("Wood screws (box of 200)", 25, 14.50),
        ],
    ],
}


def _items_to_payload(items: list[tuple[str, float, float]]) -> list[dict]:
    """Convert (description, qty, unit_price) tuples to JSON dicts the model expects."""
    return [{"description": desc, "quantity": float(qty), "unit_price": float(price)} for desc, qty, price in items]


def _seed_for_project(project: ProjectModel, creator: UserModel | None, per_project: int) -> int:
    """Seed up to `per_project` invoices for one project. Returns count actually created."""
    today = date.today()
    types_cycle = ["client", "labor", "supplier"]
    created = 0

    for i in range(per_project):
        invoice_type = types_cycle[i % 3]
        invoice_number = f"INV-{today.year}-{i + 1:04d}"

        existing = (
            db.session.query(InvoiceModel).filter_by(project_id=project.id, invoice_number=invoice_number).first()
        )
        if existing:
            print(f"    [skip] {invoice_number} already exists")
            continue

        # Use i // 3 (the "round" within this type) to vary recipient/items across cycles
        round_idx = i // 3
        recipient_name, recipient_address = RECIPIENTS[invoice_type][round_idx % len(RECIPIENTS[invoice_type])]
        items_template = ITEM_TEMPLATES[invoice_type][round_idx % len(ITEM_TEMPLATES[invoice_type])]

        # Spread issue dates across the past ~90 days for realistic filtering
        issue_date = today - timedelta(days=(i * 11) % 90)

        invoice = InvoiceModel(
            id=uuid4(),
            project_id=project.id,
            invoice_number=invoice_number,
            type=invoice_type,
            issue_date=issue_date,
            recipient_name=recipient_name,
            recipient_address=recipient_address,
            notes=f"Auto-seeded {invoice_type} invoice for testing." if i % 2 == 0 else None,
            items=_items_to_payload(items_template),
            created_by=creator.id if creator else None,
        )
        db.session.add(invoice)
        created += 1
        print(f"    [add]  {invoice_number} ({invoice_type}, {recipient_name})")

    db.session.commit()
    return created


def seed_invoices(reset: bool = False, per_project: int = 6) -> None:
    """Seed invoices for every existing project."""
    if reset:
        deleted = db.session.query(InvoiceModel).delete()
        db.session.commit()
        print(f"  [reset] deleted {deleted} existing invoices")

    projects = db.session.query(ProjectModel).all()
    if not projects:
        print("  No projects found. Run seed.py with --with-admin --with-projects first.")
        return

    # Pick any user as creator (preferably admin); falls back to None if no users.
    creator = db.session.query(UserModel).first()

    total_created = 0
    for project in projects:
        print(f"  Seeding invoices for project: {project.name}")
        total_created += _seed_for_project(project, creator, per_project)

    print(f"\n  Done. Created {total_created} new invoices across {len(projects)} project(s).")


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed invoice test data.")
    parser.add_argument("--reset", action="store_true", help="Delete all existing invoices before seeding.")
    parser.add_argument(
        "--per-project",
        type=int,
        default=6,
        help="How many invoices to create per project (default: 6, cycles through 3 types).",
    )
    args = parser.parse_args()

    app = create_app()
    with app.app_context():
        print("Seeding invoices...")
        seed_invoices(reset=args.reset, per_project=args.per_project)


if __name__ == "__main__":
    main()
