"""Integration tests for SqlAlchemyProjectDocumentRepository."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest

from app.application.project_documents.dtos import ListFiltersDTO
from app.domain.project_document import ProjectDocument
from app.infrastructure.database.repositories.sqlalchemy_project_document_repository import (
    SqlAlchemyProjectDocumentRepository,
)

# ---------------------------------------------------------------------------
# Fixtures — use the session fixture from top-level conftest (SQLite in-memory)
# ---------------------------------------------------------------------------


@pytest.fixture
def repo(session):
    """Repository wired to the per-test SQLite session."""
    return SqlAlchemyProjectDocumentRepository(session)


@pytest.fixture
def project_id() -> UUID:
    """A stable project UUID for all tests in a module run."""
    return uuid4()


@pytest.fixture
def user_id() -> UUID:
    """A stable user UUID."""
    return uuid4()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_doc(
    project_id: UUID,
    uploader_user_id: UUID,
    filename: str = "report.pdf",
    content_type: str = "application/pdf",
    size_bytes: int = 1024,
    storage_key: str | None = None,
) -> ProjectDocument:
    doc_id = uuid4()
    return ProjectDocument(
        id=doc_id,
        project_id=project_id,
        uploader_user_id=uploader_user_id,
        filename=filename,
        content_type=content_type,
        size_bytes=size_bytes,
        storage_key=storage_key or f"project-documents/{project_id}/{doc_id}/{filename}",
        created_at=datetime.now(timezone.utc),
        deleted_at=None,
    )


def _save(repo, doc: ProjectDocument) -> ProjectDocument:
    """Save and flush — session is function-scoped so no commit needed."""
    return repo.save(doc)


# ---------------------------------------------------------------------------
# Seed helpers for the integration tests — SQLite doesn't have migrations so
# we must create the project and user rows that FK constraints expect.
# ---------------------------------------------------------------------------


def _seed_project_and_user(session) -> tuple[UUID, UUID]:
    """Insert a minimal project row + user row; return (project_id, user_id)."""
    from app.infrastructure.database.models import ProjectModel, UserModel

    uid = uuid4()
    user = UserModel(
        id=uid,
        email=f"user-{uid}@test.com",
        password_hash="hash",
        is_active=True,
    )
    session.add(user)
    session.flush()

    pid = uuid4()
    project = ProjectModel(id=pid, name=f"Project {pid}", owner_id=uid)
    session.add(project)
    session.flush()

    return pid, uid


# ===========================================================================
# CRUD roundtrip
# ===========================================================================


class TestSaveAndFindById:
    def test_save_and_find_roundtrip(self, session):
        pid, uid = _seed_project_and_user(session)
        repo = SqlAlchemyProjectDocumentRepository(session)

        doc = _make_doc(project_id=pid, uploader_user_id=uid, filename="blueprint.dwg", size_bytes=2048)
        saved = _save(repo, doc)

        found = repo.find_by_id(saved.id)

        assert found is not None
        assert found.id == saved.id
        assert found.project_id == pid
        assert found.uploader_user_id == uid
        assert found.filename == "blueprint.dwg"
        assert found.size_bytes == 2048
        assert found.deleted_at is None

    def test_find_by_id_returns_none_for_missing(self, repo):
        result = repo.find_by_id(uuid4())
        assert result is None

    def test_all_fields_preserved(self, session):
        pid, uid = _seed_project_and_user(session)
        repo = SqlAlchemyProjectDocumentRepository(session)
        doc_id = uuid4()
        key = f"project-documents/{pid}/{doc_id}/photo.jpg"
        now = datetime.now(timezone.utc)

        doc = ProjectDocument(
            id=doc_id,
            project_id=pid,
            uploader_user_id=uid,
            filename="photo.jpg",
            content_type="image/jpeg",
            size_bytes=99999,
            storage_key=key,
            created_at=now,
            deleted_at=None,
        )
        _save(repo, doc)
        found = repo.find_by_id(doc_id)

        assert found.content_type == "image/jpeg"
        assert found.storage_key == key
        assert found.size_bytes == 99999


# ===========================================================================
# list_for_project
# ===========================================================================


class TestListForProject:
    def test_default_sort_most_recent_first(self, session):
        pid, uid = _seed_project_and_user(session)
        repo = SqlAlchemyProjectDocumentRepository(session)

        # Insert with slight time offset so created_at differs
        doc1 = _make_doc(pid, uid, filename="a.pdf")
        doc2 = _make_doc(pid, uid, filename="b.pdf")
        _save(repo, doc1)
        _save(repo, doc2)

        # Manually update created_at so ordering is deterministic
        from sqlalchemy import text

        session.execute(
            text("UPDATE project_documents SET created_at = :dt WHERE id = :id"),
            {"dt": "2024-01-01 10:00:00+00:00", "id": str(doc1.id)},
        )
        session.execute(
            text("UPDATE project_documents SET created_at = :dt WHERE id = :id"),
            {"dt": "2024-01-02 10:00:00+00:00", "id": str(doc2.id)},
        )
        session.flush()

        result = repo.list_for_project(pid, ListFiltersDTO(sort="created_at", order="desc"))
        ids = [d.id for d in result.items]

        assert ids.index(doc2.id) < ids.index(doc1.id)  # newer first

    def test_filter_by_kind_pdf_excludes_images(self, session):
        pid, uid = _seed_project_and_user(session)
        repo = SqlAlchemyProjectDocumentRepository(session)

        pdf_doc = _make_doc(pid, uid, filename="contract.pdf")
        img_doc = _make_doc(pid, uid, filename="photo.jpg", content_type="image/jpeg")
        _save(repo, pdf_doc)
        _save(repo, img_doc)

        result = repo.list_for_project(pid, ListFiltersDTO(kinds=("pdf",)))

        ids = [d.id for d in result.items]
        assert pdf_doc.id in ids
        assert img_doc.id not in ids

    def test_filter_by_uploader_id(self, session):
        pid, uid1 = _seed_project_and_user(session)
        _, uid2 = _seed_project_and_user(session)

        # Give uid2 access to the same project (no FK constraint on uploader in project_documents — only users FK)
        repo = SqlAlchemyProjectDocumentRepository(session)

        doc1 = _make_doc(pid, uid1, filename="doc_by_user1.pdf")
        doc2 = _make_doc(pid, uid2, filename="doc_by_user2.pdf")
        _save(repo, doc1)
        _save(repo, doc2)

        result = repo.list_for_project(pid, ListFiltersDTO(uploader_id=uid1))

        ids = [d.id for d in result.items]
        assert doc1.id in ids
        assert doc2.id not in ids

    def test_excludes_other_project_docs(self, session):
        pid1, uid = _seed_project_and_user(session)
        pid2, _ = _seed_project_and_user(session)
        repo = SqlAlchemyProjectDocumentRepository(session)

        doc_p1 = _make_doc(pid1, uid, filename="p1.pdf")
        doc_p2 = _make_doc(pid2, uid, filename="p2.pdf")
        _save(repo, doc_p1)
        _save(repo, doc_p2)

        result = repo.list_for_project(pid1, ListFiltersDTO())

        ids = [d.id for d in result.items]
        assert doc_p1.id in ids
        assert doc_p2.id not in ids

    def test_pagination_page1_returns_25_of_30(self, session):
        pid, uid = _seed_project_and_user(session)
        repo = SqlAlchemyProjectDocumentRepository(session)

        docs = []
        for i in range(30):
            doc = _make_doc(pid, uid, filename=f"file{i:02d}.pdf")
            docs.append(_save(repo, doc))

        result = repo.list_for_project(pid, ListFiltersDTO(page=1, per_page=25))

        assert len(result.items) == 25
        assert result.total == 30

    def test_pagination_page2_returns_remaining_5(self, session):
        pid, uid = _seed_project_and_user(session)
        repo = SqlAlchemyProjectDocumentRepository(session)

        for i in range(30):
            doc = _make_doc(pid, uid, filename=f"file{i:02d}.pdf")
            _save(repo, doc)

        result = repo.list_for_project(pid, ListFiltersDTO(page=2, per_page=25))

        assert len(result.items) == 5
        assert result.total == 30

    def test_filter_multiple_kinds(self, session):
        pid, uid = _seed_project_and_user(session)
        repo = SqlAlchemyProjectDocumentRepository(session)

        pdf_doc = _make_doc(pid, uid, filename="a.pdf")
        img_doc = _make_doc(pid, uid, filename="b.jpg", content_type="image/jpeg")
        doc_doc = _make_doc(
            pid,
            uid,
            filename="c.docx",
            content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
        _save(repo, pdf_doc)
        _save(repo, img_doc)
        _save(repo, doc_doc)

        result = repo.list_for_project(pid, ListFiltersDTO(kinds=("pdf", "image")))

        ids = [d.id for d in result.items]
        assert pdf_doc.id in ids
        assert img_doc.id in ids
        assert doc_doc.id not in ids


# ===========================================================================
# Soft-delete
# ===========================================================================


class TestSoftDelete:
    def test_soft_delete_sets_deleted_at(self, session):
        pid, uid = _seed_project_and_user(session)
        repo = SqlAlchemyProjectDocumentRepository(session)

        doc = _make_doc(pid, uid)
        _save(repo, doc)

        now = datetime.now(timezone.utc)
        repo.soft_delete(doc.id, now)
        session.flush()

        found = repo.find_by_id(doc.id)
        assert found is not None
        assert found.deleted_at is not None

    def test_soft_deleted_doc_excluded_from_list(self, session):
        pid, uid = _seed_project_and_user(session)
        repo = SqlAlchemyProjectDocumentRepository(session)

        doc = _make_doc(pid, uid, filename="to_delete.pdf")
        _save(repo, doc)

        repo.soft_delete(doc.id, datetime.now(timezone.utc))
        session.flush()

        result = repo.list_for_project(pid, ListFiltersDTO())
        ids = [d.id for d in result.items]
        assert doc.id not in ids

    def test_find_by_id_returns_soft_deleted_entity(self, session):
        """find_by_id is neutral — it returns soft-deleted rows; use-case filters."""
        pid, uid = _seed_project_and_user(session)
        repo = SqlAlchemyProjectDocumentRepository(session)

        doc = _make_doc(pid, uid)
        _save(repo, doc)

        repo.soft_delete(doc.id, datetime.now(timezone.utc))
        session.flush()

        found = repo.find_by_id(doc.id)
        assert found is not None
        assert found.deleted_at is not None


# ===========================================================================
# Sorting
# ===========================================================================


class TestSorting:
    def test_sort_by_size_asc(self, session):
        pid, uid = _seed_project_and_user(session)
        repo = SqlAlchemyProjectDocumentRepository(session)

        small = _make_doc(pid, uid, filename="small.pdf", size_bytes=100)
        large = _make_doc(pid, uid, filename="large.pdf", size_bytes=9999)
        _save(repo, small)
        _save(repo, large)

        result = repo.list_for_project(pid, ListFiltersDTO(sort="size", order="asc"))
        sizes = [d.size_bytes for d in result.items]
        assert sizes == sorted(sizes)

    def test_sort_by_size_desc(self, session):
        pid, uid = _seed_project_and_user(session)
        repo = SqlAlchemyProjectDocumentRepository(session)

        small = _make_doc(pid, uid, filename="small.pdf", size_bytes=100)
        large = _make_doc(pid, uid, filename="large.pdf", size_bytes=9999)
        _save(repo, small)
        _save(repo, large)

        result = repo.list_for_project(pid, ListFiltersDTO(sort="size", order="desc"))
        sizes = [d.size_bytes for d in result.items]
        assert sizes == sorted(sizes, reverse=True)

    def test_sort_by_name_asc_case_insensitive(self, session):
        pid, uid = _seed_project_and_user(session)
        repo = SqlAlchemyProjectDocumentRepository(session)

        docs = [
            _make_doc(pid, uid, filename="Zebra.pdf"),
            _make_doc(pid, uid, filename="apple.pdf"),
            _make_doc(pid, uid, filename="Mango.pdf"),
        ]
        for d in docs:
            _save(repo, d)

        result = repo.list_for_project(pid, ListFiltersDTO(sort="name", order="asc"))
        names = [d.filename.lower() for d in result.items]
        assert names == sorted(names)

    def test_sort_by_name_desc_case_insensitive(self, session):
        pid, uid = _seed_project_and_user(session)
        repo = SqlAlchemyProjectDocumentRepository(session)

        docs = [
            _make_doc(pid, uid, filename="Zebra.pdf"),
            _make_doc(pid, uid, filename="apple.pdf"),
            _make_doc(pid, uid, filename="Mango.pdf"),
        ]
        for d in docs:
            _save(repo, d)

        result = repo.list_for_project(pid, ListFiltersDTO(sort="name", order="desc"))
        names = [d.filename.lower() for d in result.items]
        assert names == sorted(names, reverse=True)

    def test_sort_by_uploader(self, session):
        """sort=uploader exercises the uploader sort branch (line 150 in repo)."""
        pid, uid1 = _seed_project_and_user(session)
        _, uid2 = _seed_project_and_user(session)
        repo = SqlAlchemyProjectDocumentRepository(session)

        doc1 = _make_doc(pid, uid1, filename="doc_u1.pdf")
        doc2 = _make_doc(pid, uid2, filename="doc_u2.pdf")
        _save(repo, doc1)
        _save(repo, doc2)

        result = repo.list_for_project(pid, ListFiltersDTO(sort="uploader", order="asc"))
        # Should return 2 docs without error
        assert len(result.items) == 2


# ===========================================================================
# Kind filter — "other" branch (covers lines 108-120 in repository)
# ===========================================================================


class TestKindFilterOther:
    def test_filter_other_returns_unknown_extensions(self, session):
        """kind='other' should return files whose extension is not in any known list."""
        pid, uid = _seed_project_and_user(session)
        repo = SqlAlchemyProjectDocumentRepository(session)

        # Known kind — should be excluded from 'other' results
        known_doc = _make_doc(pid, uid, filename="document.pdf")
        # Unknown extension — should appear in 'other' results
        other_doc = _make_doc(pid, uid, filename="archive.unknown_ext")
        _save(repo, known_doc)
        _save(repo, other_doc)

        result = repo.list_for_project(pid, ListFiltersDTO(kinds=("other",)))

        ids = [d.id for d in result.items]
        assert other_doc.id in ids
        assert known_doc.id not in ids

    def test_filter_pdf_and_other_together(self, session):
        """kinds=('pdf', 'other') returns both pdf files and unknown-extension files."""
        pid, uid = _seed_project_and_user(session)
        repo = SqlAlchemyProjectDocumentRepository(session)

        pdf_doc = _make_doc(pid, uid, filename="contract.pdf")
        img_doc = _make_doc(pid, uid, filename="photo.jpg", content_type="image/jpeg")
        other_doc = _make_doc(pid, uid, filename="data.xyz")
        _save(repo, pdf_doc)
        _save(repo, img_doc)
        _save(repo, other_doc)

        result = repo.list_for_project(pid, ListFiltersDTO(kinds=("pdf", "other")))

        ids = [d.id for d in result.items]
        assert pdf_doc.id in ids
        assert other_doc.id in ids
        assert img_doc.id not in ids


# ---------------------------------------------------------------------------
# Janitor: find_soft_deleted_before + hard_delete (PurgeSoftDeletedDocumentsUseCase)
# ---------------------------------------------------------------------------


class TestFindSoftDeletedBefore:
    def test_returns_only_soft_deleted_older_than_cutoff(self, session):
        from datetime import timedelta

        pid, uid = _seed_project_and_user(session)
        repo = SqlAlchemyProjectDocumentRepository(session)

        now = datetime.now(timezone.utc)
        # active (not deleted)
        active = _make_doc(pid, uid, filename="active.pdf")
        _save(repo, active)
        # soft-deleted 100 days ago
        old = _make_doc(pid, uid, filename="old.pdf")
        _save(repo, old)
        repo.soft_delete(old.id, now - timedelta(days=100))
        # soft-deleted 10 days ago
        recent = _make_doc(pid, uid, filename="recent.pdf")
        _save(repo, recent)
        repo.soft_delete(recent.id, now - timedelta(days=10))

        cutoff = now - timedelta(days=30)
        result = repo.find_soft_deleted_before(cutoff)

        ids = [d.id for d in result]
        assert old.id in ids
        assert recent.id not in ids
        assert active.id not in ids

    def test_orders_oldest_first(self, session):
        from datetime import timedelta

        pid, uid = _seed_project_and_user(session)
        repo = SqlAlchemyProjectDocumentRepository(session)

        now = datetime.now(timezone.utc)
        older = _make_doc(pid, uid, filename="older.pdf")
        newer = _make_doc(pid, uid, filename="newer.pdf")
        _save(repo, older)
        _save(repo, newer)
        repo.soft_delete(older.id, now - timedelta(days=200))
        repo.soft_delete(newer.id, now - timedelta(days=100))

        result = repo.find_soft_deleted_before(now - timedelta(days=30))

        # Oldest deleted_at first (older was deleted 200 days ago).
        assert [d.id for d in result] == [older.id, newer.id]

    def test_respects_limit(self, session):
        from datetime import timedelta

        pid, uid = _seed_project_and_user(session)
        repo = SqlAlchemyProjectDocumentRepository(session)

        now = datetime.now(timezone.utc)
        for i in range(5):
            doc = _make_doc(pid, uid, filename=f"f{i}.pdf")
            _save(repo, doc)
            repo.soft_delete(doc.id, now - timedelta(days=100 + i))

        result = repo.find_soft_deleted_before(now - timedelta(days=30), limit=2)
        assert len(result) == 2


class TestHardDelete:
    def test_removes_row_completely(self, session):
        pid, uid = _seed_project_and_user(session)
        repo = SqlAlchemyProjectDocumentRepository(session)

        doc = _make_doc(pid, uid)
        _save(repo, doc)
        assert repo.find_by_id(doc.id) is not None

        repo.hard_delete(doc.id)

        assert repo.find_by_id(doc.id) is None

    def test_idempotent_on_missing(self, session):
        pid, uid = _seed_project_and_user(session)
        repo = SqlAlchemyProjectDocumentRepository(session)

        # Hard-delete a non-existent id — must not raise.
        repo.hard_delete(uuid4())

    def test_after_purge_find_soft_deleted_excludes_row(self, session):
        from datetime import timedelta

        pid, uid = _seed_project_and_user(session)
        repo = SqlAlchemyProjectDocumentRepository(session)

        now = datetime.now(timezone.utc)
        doc = _make_doc(pid, uid)
        _save(repo, doc)
        repo.soft_delete(doc.id, now - timedelta(days=100))

        repo.hard_delete(doc.id)

        assert doc.id not in [d.id for d in repo.find_soft_deleted_before(now)]
