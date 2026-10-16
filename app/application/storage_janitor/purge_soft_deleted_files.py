"""Use case: permanently remove soft-deleted documents, photos and analyses.

Deleting a document, photo/video or analysis only sets ``deleted_at``: the row
disappears from every list but the stored object(s) stay. Once the retention
window has passed, this janitor deletes the object(s) (original, thumbnail or
poster) and then the row. Storage deletes are idempotent, so a partly purged
batch can be re-run; a row whose storage delete fails stays soft-deleted and is
reported.

Run daily by ``scripts/purge_soft_deleted_files.py`` (see the deploy repo's
cron installer).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Protocol, Sequence, runtime_checkable
from uuid import UUID

from app.application.project_documents.ports import IDocumentStorage, ITransactionalSession

_log = logging.getLogger(__name__)

# How long a deleted file is kept before it is erased for good.
DEFAULT_RETENTION_DAYS = 30


@dataclass(frozen=True)
class PurgeCandidate:
    """One soft-deleted row and every storage object it owns."""

    id: UUID
    storage_keys: tuple[str, ...]


@runtime_checkable
class ISoftDeletedFileSource(Protocol):
    """A table of soft-deletable rows that own storage objects."""

    name: str

    def find_soft_deleted_before(self, cutoff: datetime, limit: int) -> list[PurgeCandidate]:
        """Rows with ``deleted_at < cutoff``, oldest first."""
        ...

    def hard_delete(self, row_id: UUID) -> None:
        """Delete the row (its tags cascade); the caller commits."""
        ...


@dataclass
class SourcePurgeResult:
    """Outcome for one source."""

    candidates: int = 0
    purged: int = 0
    failed: list[tuple[UUID, str]] = field(default_factory=list)


class PurgeSoftDeletedFilesUseCase:
    """Erase rows (and their stored files) soft-deleted more than ``retention_days`` ago."""

    def __init__(
        self,
        sources: Sequence[ISoftDeletedFileSource],
        storage: IDocumentStorage,
        db_session: ITransactionalSession,
    ) -> None:
        self._sources = sources
        self._storage = storage
        self._db_session = db_session

    def execute(
        self,
        *,
        retention_days: int = DEFAULT_RETENTION_DAYS,
        batch_size: int = 1000,
        dry_run: bool = False,
        now: datetime | None = None,
    ) -> dict[str, SourcePurgeResult]:
        if retention_days <= 0:
            raise ValueError("retention_days must be > 0 (refuse to purge immediately-deleted rows)")
        cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=retention_days)

        results: dict[str, SourcePurgeResult] = {}
        for source in self._sources:
            result = SourcePurgeResult()
            results[source.name] = result
            candidates = source.find_soft_deleted_before(cutoff, batch_size)
            result.candidates = len(candidates)
            if dry_run:
                continue
            for candidate in candidates:
                try:
                    for key in candidate.storage_keys:
                        self._storage.delete(key)
                    source.hard_delete(candidate.id)
                    result.purged += 1
                except Exception as exc:  # noqa: BLE001 — one bad row must not stop the janitor
                    _log.warning("Janitor: failed to purge %s %s: %s", source.name, candidate.id, exc)
                    result.failed.append((candidate.id, f"{type(exc).__name__}: {exc}"))
            self._db_session.commit()
        return results
