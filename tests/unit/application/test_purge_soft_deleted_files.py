"""PurgeSoftDeletedFilesUseCase (QA3 documents-photos-analyses-04 / config-deploy-22)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest

from app.application.storage_janitor import PurgeCandidate, PurgeSoftDeletedFilesUseCase

NOW = datetime(2026, 10, 16, tzinfo=timezone.utc)


class FakeSource:
    def __init__(self, name: str, rows: list[tuple[PurgeCandidate, datetime]]) -> None:
        self.name = name
        self.rows = rows
        self.deleted: list[UUID] = []

    def find_soft_deleted_before(self, cutoff: datetime, limit: int) -> list[PurgeCandidate]:
        return [c for c, deleted_at in self.rows if deleted_at < cutoff][:limit]

    def hard_delete(self, row_id: UUID) -> None:
        self.deleted.append(row_id)


def _candidate(*keys: str) -> PurgeCandidate:
    return PurgeCandidate(id=uuid4(), storage_keys=keys)


def test_purges_old_rows_and_all_their_objects():
    old_photo = _candidate("p/original/a.jpg", "p/thumb.jpg")
    recent_photo = _candidate("p2/original/b.jpg", "p2/thumb.jpg")
    old_analysis = _candidate("a/x.html")
    photos = FakeSource("photos", [(old_photo, NOW - timedelta(days=40)), (recent_photo, NOW - timedelta(days=2))])
    analyses = FakeSource("analyses", [(old_analysis, NOW - timedelta(days=31))])
    storage, session = MagicMock(), MagicMock()

    results = PurgeSoftDeletedFilesUseCase([photos, analyses], storage, session).execute(retention_days=30, now=NOW)

    deleted_keys = [c.args[0] for c in storage.delete.call_args_list]
    assert deleted_keys == ["p/original/a.jpg", "p/thumb.jpg", "a/x.html"]
    assert photos.deleted == [old_photo.id]
    assert analyses.deleted == [old_analysis.id]
    assert results["photos"].candidates == 1 and results["photos"].purged == 1
    assert session.commit.call_count == 2


def test_storage_failure_keeps_the_row_and_continues():
    bad, good = _candidate("bad"), _candidate("good")
    source = FakeSource("documents", [(bad, NOW - timedelta(days=90)), (good, NOW - timedelta(days=90))])
    storage = MagicMock()
    storage.delete.side_effect = lambda key: (_ for _ in ()).throw(OSError("s3 down")) if key == "bad" else None

    results = PurgeSoftDeletedFilesUseCase([source], storage, MagicMock()).execute(retention_days=30, now=NOW)

    assert source.deleted == [good.id]
    assert results["documents"].purged == 1
    assert results["documents"].failed[0][0] == bad.id


def test_dry_run_touches_nothing():
    source = FakeSource("photos", [(_candidate("k"), NOW - timedelta(days=90))])
    storage, session = MagicMock(), MagicMock()
    results = PurgeSoftDeletedFilesUseCase([source], storage, session).execute(dry_run=True, now=NOW)
    assert results["photos"].candidates == 1
    storage.delete.assert_not_called()
    assert source.deleted == []


def test_zero_retention_is_refused():
    with pytest.raises(ValueError):
        PurgeSoftDeletedFilesUseCase([], MagicMock(), MagicMock()).execute(retention_days=0)


def test_sweep_deletes_only_unowned_old_objects():
    """QA3 documents-photos-analyses-28: abandoned presigned uploads are removed."""
    from app.application.storage_janitor.sweep_orphaned_uploads import SweepOrphanedUploadsUseCase

    storage = MagicMock()
    storage.list_keys_older_than.return_value = iter(["project-documents/p/a/x.pdf", "project-documents/p/b/y.pdf"])
    known = lambda keys: {"project-documents/p/a/x.pdf"}  # noqa: E731

    found, deleted = SweepOrphanedUploadsUseCase(storage, known).execute(now=NOW)

    assert (found, deleted) == (1, 1)
    storage.delete.assert_called_once_with("project-documents/p/b/y.pdf")
    assert storage.list_keys_older_than.call_args.args[1] == NOW - timedelta(days=1)
