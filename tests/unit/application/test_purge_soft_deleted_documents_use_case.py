"""Unit tests for PurgeSoftDeletedDocumentsUseCase."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from app.application.project_documents.ports import IDocumentStorage, IProjectDocumentRepository
from app.application.project_documents.purge_soft_deleted_documents import (
    PurgeSoftDeletedDocumentsUseCase,
)
from app.domain.project_document import ProjectDocument


def _doc(deleted_days_ago: int) -> ProjectDocument:
    now = datetime.now(timezone.utc)
    return ProjectDocument(
        id=uuid4(),
        project_id=uuid4(),
        uploader_user_id=uuid4(),
        filename="x.pdf",
        content_type="application/pdf",
        size_bytes=10,
        storage_key=f"project-documents/x/y/{uuid4()}.pdf",
        created_at=now - timedelta(days=deleted_days_ago + 1),
        deleted_at=now - timedelta(days=deleted_days_ago),
    )


def _make_use_case(repo=None, storage=None, session=None):
    repo = repo or MagicMock(spec=IProjectDocumentRepository)
    storage = storage or MagicMock(spec=IDocumentStorage)
    session = session or MagicMock()
    uc = PurgeSoftDeletedDocumentsUseCase(repo=repo, storage=storage, db_session=session)
    return uc, repo, storage, session


class TestRetentionGuard:
    def test_zero_retention_days_raises(self):
        uc, _, _, _ = _make_use_case()
        with pytest.raises(ValueError):
            uc.execute(retention_days=0)

    def test_negative_retention_days_raises(self):
        uc, _, _, _ = _make_use_case()
        with pytest.raises(ValueError):
            uc.execute(retention_days=-1)


class TestDryRun:
    def test_dry_run_counts_candidates_without_writes(self):
        uc, repo, storage, session = _make_use_case()
        candidates = [_doc(91), _doc(95), _doc(120)]
        repo.find_soft_deleted_before.return_value = candidates

        result = uc.execute(retention_days=90, dry_run=True)

        assert result.dry_run is True
        assert result.candidates == 3
        assert result.purged == 0
        assert result.failed == []
        storage.delete.assert_not_called()
        repo.hard_delete.assert_not_called()
        session.commit.assert_not_called()

    def test_dry_run_with_no_candidates(self):
        uc, repo, storage, session = _make_use_case()
        repo.find_soft_deleted_before.return_value = []

        result = uc.execute(retention_days=90, dry_run=True)

        assert result.candidates == 0
        assert result.purged == 0


class TestPurgeHappyPath:
    def test_purges_storage_and_db_for_each_candidate(self):
        uc, repo, storage, session = _make_use_case()
        candidates = [_doc(91), _doc(95), _doc(120)]
        repo.find_soft_deleted_before.return_value = candidates

        result = uc.execute(retention_days=90)

        assert result.dry_run is False
        assert result.candidates == 3
        assert result.purged == 3
        assert result.failed == []
        assert storage.delete.call_count == 3
        assert repo.hard_delete.call_count == 3
        session.commit.assert_called_once()

    def test_storage_called_before_hard_delete(self):
        uc, repo, storage, session = _make_use_case()
        doc = _doc(91)
        repo.find_soft_deleted_before.return_value = [doc]

        call_order: list[str] = []
        storage.delete.side_effect = lambda key: call_order.append(f"storage:{key}")
        repo.hard_delete.side_effect = lambda doc_id: call_order.append(f"db:{doc_id}")

        uc.execute(retention_days=90)

        assert call_order[0].startswith("storage:")
        assert call_order[1].startswith("db:")


class TestPurgeFailures:
    def test_storage_failure_records_but_continues(self):
        uc, repo, storage, session = _make_use_case()
        good1, bad, good2 = _doc(91), _doc(92), _doc(93)
        repo.find_soft_deleted_before.return_value = [good1, bad, good2]

        def fail_for_bad(key: str):
            if key == bad.storage_key:
                raise OSError("MinIO unavailable")

        storage.delete.side_effect = fail_for_bad

        result = uc.execute(retention_days=90)

        assert result.candidates == 3
        assert result.purged == 2  # good1 + good2
        assert len(result.failed) == 1
        assert result.failed[0].document_id == bad.id
        assert "OSError" in result.failed[0].reason
        # hard_delete called only for the two that succeeded in storage
        assert repo.hard_delete.call_count == 2
        # Commit still called to persist the successful purges
        session.commit.assert_called_once()

    def test_db_failure_records_but_continues(self):
        uc, repo, storage, session = _make_use_case()
        good1, bad, good2 = _doc(91), _doc(92), _doc(93)
        repo.find_soft_deleted_before.return_value = [good1, bad, good2]

        def fail_for_bad(doc_id):
            if doc_id == bad.id:
                raise RuntimeError("DB connection lost")

        repo.hard_delete.side_effect = fail_for_bad

        result = uc.execute(retention_days=90)

        assert result.candidates == 3
        assert result.purged == 2
        assert len(result.failed) == 1
        assert result.failed[0].document_id == bad.id

    def test_all_failures_returns_zero_purged(self):
        uc, repo, storage, session = _make_use_case()
        candidates = [_doc(91), _doc(95)]
        repo.find_soft_deleted_before.return_value = candidates
        storage.delete.side_effect = OSError("network down")

        result = uc.execute(retention_days=90)

        assert result.purged == 0
        assert len(result.failed) == 2
        assert result.ok is False


class TestCutoffComputation:
    def test_cutoff_passed_to_repo(self):
        uc, repo, storage, session = _make_use_case()
        repo.find_soft_deleted_before.return_value = []
        fixed_now = datetime(2026, 5, 17, 12, 0, 0, tzinfo=timezone.utc)

        uc.execute(retention_days=30, now=fixed_now)

        call_args = repo.find_soft_deleted_before.call_args
        passed_cutoff = call_args[0][0]
        assert passed_cutoff == datetime(2026, 4, 17, 12, 0, 0, tzinfo=timezone.utc)

    def test_batch_size_passed_to_repo(self):
        uc, repo, _, _ = _make_use_case()
        repo.find_soft_deleted_before.return_value = []

        uc.execute(retention_days=30, batch_size=42)

        # The keyword `limit=` is what the port expects.
        call_kwargs = repo.find_soft_deleted_before.call_args[1]
        assert call_kwargs.get("limit") == 42


class TestEmptyResult:
    def test_no_candidates_returns_zero_purged(self):
        uc, repo, storage, session = _make_use_case()
        repo.find_soft_deleted_before.return_value = []

        result = uc.execute(retention_days=90)

        assert result.candidates == 0
        assert result.purged == 0
        assert result.failed == []
        assert result.ok is True
        # commit STILL called — empty transactions are benign
        storage.delete.assert_not_called()
        repo.hard_delete.assert_not_called()
