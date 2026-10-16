"""Give every project of a company every active worker of the company.

Workers are stored per project but the people belong to the company. Run this once
after moving a project to another company, or to repair a roster. Idempotent: it only
adds missing rows; a person already on a project (active or not) is never touched.

Usage:
  uv run python scripts/sync_company_workers.py                 # every company
  uv run python scripts/sync_company_workers.py COMPANY_UUID    # one company
"""

import sys
from pathlib import Path
from uuid import UUID

from sqlalchemy import text

from app import create_app, db


def get_container():
    """The DI container from wiring.py at the repo root.

    `python scripts/<name>.py` puts scripts/ first on sys.path, not the repo root (the editable
    install maps only app/ and config/), so `import wiring` failed when cron or
    `docker compose exec` started the script that way. Add the root before importing.
    """
    repo_root = str(Path(__file__).resolve().parent.parent)
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)
    from wiring import get_container as wiring_container

    return wiring_container()


def main(argv: list[str]) -> int:
    app = create_app()
    with app.app_context():
        usecase = get_container().enroll_company_workers_usecase
        if argv:
            company_ids = [UUID(argv[0])]
        else:
            company_ids = [r[0] for r in db.session.execute(text("SELECT id FROM companies ORDER BY legal_name"))]
        total = 0
        for company_id in company_ids:
            added = usecase.sync_company(company_id)
            total += added
            print(f"{company_id}: {added} worker rows added")
        print(f"Total: {total}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
