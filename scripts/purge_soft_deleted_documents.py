"""Janitor: permanently delete project_documents whose soft-deletion has aged out.

Soft-deletion sets `project_documents.deleted_at`; the MinIO object is retained
indefinitely. This script is the operator-run cleanup that finalizes the
removal once the retention window has passed.

Usage (from folio-back-end/):

    # Dry-run — report what WOULD be purged, no writes
    uv run python scripts/purge_soft_deleted_documents.py --retention-days 90 --dry-run

    # Real run — purges up to --batch-size (default 1000) rows per invocation.
    # Re-run until "purged 0" to drain the backlog.
    uv run python scripts/purge_soft_deleted_documents.py --retention-days 90

Exit codes:
    0  — success (including partial-failure where some rows could not be
         storage-deleted but others were purged; failures are logged).
    1  — bad CLI args.
    2  — every candidate row failed (the run produced zero successful purges
         AND there were failures; treat as ops alert).
"""

from __future__ import annotations

import argparse
import logging
import sys

from app import create_app
from wiring import get_container


def main() -> int:
    parser = argparse.ArgumentParser(description="Purge soft-deleted project_documents past retention.")
    parser.add_argument(
        "--retention-days",
        type=int,
        required=True,
        help="Minimum age in days since deleted_at for a row to be purged (must be > 0).",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=1000,
        help="Maximum rows processed in this invocation (default 1000).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report candidate count without performing any deletes.",
    )
    args = parser.parse_args()

    if args.retention_days <= 0:
        print("ERROR: --retention-days must be > 0", file=sys.stderr)
        return 1
    if args.batch_size <= 0:
        print("ERROR: --batch-size must be > 0", file=sys.stderr)
        return 1

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    log = logging.getLogger("janitor.project_documents")

    app = create_app()
    with app.app_context():
        container = get_container()
        if container.purge_soft_deleted_documents_usecase is None:
            print("ERROR: PurgeSoftDeletedDocumentsUseCase is not wired", file=sys.stderr)
            return 1

        log.info(
            "Starting janitor: retention_days=%d batch_size=%d dry_run=%s",
            args.retention_days,
            args.batch_size,
            args.dry_run,
        )
        result = container.purge_soft_deleted_documents_usecase.execute(
            retention_days=args.retention_days,
            batch_size=args.batch_size,
            dry_run=args.dry_run,
        )

        log.info(
            "Janitor finished: candidates=%d purged=%d failed=%d dry_run=%s",
            result.candidates,
            result.purged,
            len(result.failed),
            result.dry_run,
        )
        for failure in result.failed:
            log.warning(
                "  failed: doc_id=%s storage_key=%s reason=%s",
                failure.document_id,
                failure.storage_key,
                failure.reason,
            )

        if result.failed and result.purged == 0:
            return 2
        return 0


if __name__ == "__main__":
    sys.exit(main())
