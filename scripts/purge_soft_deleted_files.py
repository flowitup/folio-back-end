"""Janitor: erase soft-deleted documents, photos/videos and analyses past retention.

Deleting a file in the app hides it at once (soft delete); its stored objects
are erased by this job once ``--retention-days`` (default 30) have passed.
Scheduled daily by the deploy repo (scripts/backup/install-backup-cron.sh in
flowitup/folio), which runs it inside the API container:

    docker compose run --rm api python scripts/purge_soft_deleted_files.py

Usage (from folio-back-end/):

    uv run python scripts/purge_soft_deleted_files.py --dry-run
    uv run python scripts/purge_soft_deleted_files.py --retention-days 30

Exit codes: 0 success (failures are logged), 1 bad arguments,
2 a source had failures and purged nothing (alert).
"""

from __future__ import annotations

import argparse
import logging
import sys

from app import create_app, db
from app.application.storage_janitor import PurgeSoftDeletedFilesUseCase
from app.application.storage_janitor.purge_soft_deleted_files import DEFAULT_RETENTION_DAYS
from app.infrastructure.database.repositories.soft_deleted_file_sources import (
    SoftDeletedAnalysisSource,
    SoftDeletedDocumentSource,
    SoftDeletedPhotoSource,
)
from wiring import get_container


def main() -> int:
    parser = argparse.ArgumentParser(description="Erase soft-deleted documents, photos and analyses past retention.")
    parser.add_argument("--retention-days", type=int, default=DEFAULT_RETENTION_DAYS)
    parser.add_argument("--batch-size", type=int, default=1000)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.retention_days <= 0 or args.batch_size <= 0:
        print("ERROR: --retention-days and --batch-size must be > 0", file=sys.stderr)
        return 1

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    log = logging.getLogger("janitor.soft_deleted_files")

    app = create_app()
    with app.app_context():
        storage = get_container().document_storage
        if storage is None:
            print("ERROR: document storage is not wired", file=sys.stderr)
            return 1
        session = db.session
        usecase = PurgeSoftDeletedFilesUseCase(
            sources=[
                SoftDeletedDocumentSource(session),
                SoftDeletedPhotoSource(session),
                SoftDeletedAnalysisSource(session),
            ],
            storage=storage,
            db_session=session,
        )
        log.info(
            "Starting: retention_days=%d batch_size=%d dry_run=%s", args.retention_days, args.batch_size, args.dry_run
        )
        results = usecase.execute(retention_days=args.retention_days, batch_size=args.batch_size, dry_run=args.dry_run)

    alert = False
    for name, result in results.items():
        log.info("%s: candidates=%d purged=%d failed=%d", name, result.candidates, result.purged, len(result.failed))
        for row_id, reason in result.failed:
            log.warning("  %s %s: %s", name, row_id, reason)
        alert = alert or (bool(result.failed) and result.purged == 0)
    return 2 if alert else 0


if __name__ == "__main__":
    sys.exit(main())
