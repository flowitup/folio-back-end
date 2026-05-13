"""Backfill workers.person_id from the existing worker.name + worker.phone.

For every Worker with NULL person_id, link it to an existing Person whose
identity matches, or create a fresh Person and link to it.

Identity match rules (in order):
  1. Phone match — if worker.phone is non-null and a Person has the same
     phone (regardless of name), reuse that Person.
  2. Normalized-name match — if no phone match, look up Persons whose
     normalized_name = lower(trim(worker.name)). If exactly one such
     Person exists with the same NULL/non-NULL phone signature, reuse it.
  3. Otherwise — create a new Person owned by the project owner, link.

Edge cases producing AMBIGUITY (logged for admin review, not auto-linked):
  * Worker has no phone, multiple Persons match the normalized_name with
    different phones → leave worker.person_id NULL; admin uses merge tool.
  * Worker has a phone, multiple Persons share that phone with different
    names → leave NULL; admin investigates.

Idempotent: workers that already have person_id are skipped.

Usage:
  uv run python scripts/backfill_persons.py            # link + report
  uv run python scripts/backfill_persons.py --csv FILE  # also emit CSV

Phase 1c of plan 260512-2341-labor-calendar-and-bulk-log.
"""

import csv
import sys
from collections import defaultdict
from datetime import datetime, timezone
from typing import Dict, List, Optional
from uuid import uuid4

from app import create_app, db
from app.domain.entities.person import Person
from app.infrastructure.database.models import (
    PersonModel,
    ProjectModel,
    UserModel,
    WorkerModel,
)


def _arg_value(flag: str) -> Optional[str]:
    """Return the value after ``flag`` in argv, or None."""
    try:
        i = sys.argv.index(flag)
    except ValueError:
        return None
    return sys.argv[i + 1] if i + 1 < len(sys.argv) else None


def _resolve_owner(worker: WorkerModel) -> Optional[UserModel]:
    """Project owner of the worker — used as Person.created_by_user_id when
    we have to create a new Person for this worker."""
    project = db.session.query(ProjectModel).filter(ProjectModel.id == worker.project_id).first()
    if not project:
        return None
    return db.session.query(UserModel).filter(UserModel.id == project.owner_id).first()


def _find_match(worker: WorkerModel) -> tuple[Optional[PersonModel], str]:
    """Return (matched_person, classification).

    classification ∈ {phone, name, ambiguous, none}
    """
    normalized = Person.normalize(worker.name)

    # Rule 1: phone match
    if worker.phone:
        rows = db.session.query(PersonModel).filter(PersonModel.phone == worker.phone).all()
        if len(rows) == 1:
            return rows[0], "phone"
        if len(rows) > 1:
            return None, "ambiguous"

    # Rule 2: normalized-name match. Only applies when the worker has no
    # phone (if it had one, Rule 1 already had its chance). We require the
    # candidate Person to also have NULL phone — otherwise we'd risk
    # linking a phoneless Worker to a different physical person who happens
    # to share the same name but has a known phone elsewhere.
    if not worker.phone:
        name_rows = (
            db.session.query(PersonModel)
            .filter(PersonModel.normalized_name == normalized)
            .filter(PersonModel.phone.is_(None))
            .all()
        )
        if len(name_rows) == 1:
            return name_rows[0], "name"
        if len(name_rows) > 1:
            return None, "ambiguous"

    return None, "none"


def backfill() -> Dict[str, int]:
    """Run the backfill. Returns counters for the summary report."""
    counters = defaultdict(int)
    ambiguous: List[dict] = []

    workers = db.session.query(WorkerModel).filter(WorkerModel.person_id.is_(None)).all()
    counters["workers_scanned"] = len(workers)

    now = datetime.now(timezone.utc)

    for w in workers:
        match, classification = _find_match(w)

        if classification == "ambiguous":
            counters["ambiguous_skipped"] += 1
            ambiguous.append(
                {
                    "worker_id": str(w.id),
                    "worker_name": w.name,
                    "worker_phone": w.phone or "",
                    "reason": "multiple_candidate_persons",
                }
            )
            continue

        if match is None:
            owner = _resolve_owner(w)
            if owner is None:
                counters["no_owner_skipped"] += 1
                ambiguous.append(
                    {
                        "worker_id": str(w.id),
                        "worker_name": w.name,
                        "worker_phone": w.phone or "",
                        "reason": "project_owner_not_found",
                    }
                )
                continue

            new_person = PersonModel(
                id=uuid4(),
                name=w.name,
                phone=w.phone,
                normalized_name=Person.normalize(w.name),
                created_by_user_id=owner.id,
                created_at=now,
                updated_at=now,
            )
            db.session.add(new_person)
            db.session.flush()
            w.person_id = new_person.id
            counters["persons_created"] += 1
        else:
            w.person_id = match.id
            counters[f"linked_by_{classification}"] += 1

    db.session.commit()

    # Optional CSV report — admins use this to audit auto-created Persons
    # and resolve ambiguous workers via the merge tool.
    csv_path = _arg_value("--csv")
    if csv_path and ambiguous:
        with open(csv_path, "w", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=["worker_id", "worker_name", "worker_phone", "reason"])
            writer.writeheader()
            writer.writerows(ambiguous)
        counters["ambiguous_rows_written_to_csv"] = len(ambiguous)
        print(f"  Wrote {len(ambiguous)} ambiguous rows to {csv_path}")

    return dict(counters)


def main() -> None:
    app = create_app()
    with app.app_context():
        print("Backfilling workers.person_id...")
        counters = backfill()
        print("\nSummary:")
        for k, v in sorted(counters.items()):
            print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
