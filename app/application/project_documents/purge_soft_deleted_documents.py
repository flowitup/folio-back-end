"""Use case: purge soft-deleted documents past their retention window.

Operator-run via `scripts/purge_soft_deleted_documents.py`. Removes the MinIO
object first, then hard-deletes the DB row. If storage delete fails for one
document, the run continues for the others — the failed row stays soft-deleted
and surfaces in the return value so the operator can investigate.

Storage deletes are idempotent (S3 `delete_object` returns success for
already-missing keys), so re-running the janitor against a partially-deleted
batch is safe.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from app.application.project_documents.dtos import PurgeFailureDTO, PurgeResultDTO
from app.application.project_documents.ports import (
    IDocumentStorage,
    IProjectDocumentRepository,
    ITransactionalSession,
)

_log = logging.getLogger(__name__)


class PurgeSoftDeletedDocumentsUseCase:
    """Hard-delete project documents whose soft-deletion exceeds the retention window."""

    def __init__(
        self,
        repo: IProjectDocumentRepository,
        storage: IDocumentStorage,
        db_session: ITransactionalSession,
    ) -> None:
        self._repo = repo
        self._storage = storage
        self._db_session = db_session

    def execute(
        self,
        *,
        retention_days: int,
        batch_size: int = 1000,
        dry_run: bool = False,
        now: datetime | None = None,
    ) -> PurgeResultDTO:
        """Purge soft-deleted documents older than `retention_days` days.

        Args:
            retention_days: minimum age (in days) since `deleted_at` for a row
                to be purged. Must be > 0 — there is no opt-out for "purge all
                soft-deleted immediately" to prevent foot-guns.
            batch_size: max rows processed per call. Operator can re-run.
            dry_run: if True, only count candidates — no storage or DB writes.
            now: clock override for tests. Defaults to `datetime.now(timezone.utc)`.

        Returns:
            PurgeResultDTO with candidate count, successful purge count, list
            of failed documents (with reason), and the dry_run flag.

        Raises:
            ValueError: if retention_days <= 0.
        """
        if retention_days <= 0:
            raise ValueError("retention_days must be > 0 (refuse to purge immediately-deleted rows)")

        when = now or datetime.now(timezone.utc)
        cutoff = when - timedelta(days=retention_days)
        candidates = self._repo.find_soft_deleted_before(cutoff, limit=batch_size)

        if dry_run:
            return PurgeResultDTO(
                candidates=len(candidates),
                purged=0,
                failed=[],
                dry_run=True,
            )

        purged = 0
        failed: list[PurgeFailureDTO] = []
        for doc in candidates:
            try:
                # Storage first — if it fails, the DB row stays so we don't
                # orphan the MinIO object beyond a re-runnable cleanup.
                self._storage.delete(doc.storage_key)
                self._repo.hard_delete(doc.id)
                purged += 1
            except Exception as e:  # noqa: BLE001 — janitor MUST NOT crash on bad rows
                _log.warning(
                    "Janitor: failed to purge document %s (storage_key=%s): %s",
                    doc.id,
                    doc.storage_key,
                    e,
                )
                failed.append(
                    PurgeFailureDTO(
                        document_id=doc.id,
                        storage_key=doc.storage_key,
                        reason=type(e).__name__ + ": " + str(e),
                    )
                )

        # Commit the hard-deletes that succeeded.
        self._db_session.commit()

        return PurgeResultDTO(
            candidates=len(candidates),
            purged=purged,
            failed=failed,
            dry_run=False,
        )
