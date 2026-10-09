"""Deleting a project removes the stored files its cascaded rows pointed at."""

from __future__ import annotations

from datetime import date, datetime, timezone
from io import BytesIO
from uuid import uuid4

import pytest

from app.application.projects.delete import DeleteProjectUseCase
from app.infrastructure.adapters.in_memory_document_storage import InMemoryDocumentStorage
from app.infrastructure.adapters.sqlalchemy_project import SQLAlchemyProjectRepository
from app.infrastructure.database.models import (
    ChiffrageArticleModel,
    ChiffragePosteModel,
    InvoiceAttachmentModel,
    InvoiceModel,
    ProjectAnalysisModel,
    ProjectDocumentModel,
    ProjectModel,
    ProjectPhotoRow,
    UserModel,
)
from app.infrastructure.database.repositories.sqlalchemy_project_storage_key_reader import (
    SqlAlchemyProjectStorageKeyReader,
)
from tests.company_tenancy_helper import company_for_projects


def _project_with_files(session, owner, company_id, tag: str):
    """A project with one row of every file-bearing kind; returns (project_id, keys)."""
    project = ProjectModel(id=uuid4(), name=f"P {tag}", owner_id=owner.id, company_id=company_id)
    session.add(project)
    session.flush()
    pid = project.id
    keys = [
        f"project-documents/{pid}/live.pdf",
        f"project-documents/{pid}/trashed.pdf",
        f"project-photos/{pid}/original/a.jpg",
        f"project-photos/{pid}/thumb.jpg",
        f"project-analyses/{pid}/a.html",
        f"chiffrage-articles/{tag}/image",
    ]
    now = datetime.now(timezone.utc)
    doc = dict(project_id=pid, uploader_user_id=owner.id, content_type="application/pdf", size_bytes=1)
    session.add(ProjectDocumentModel(filename="live.pdf", storage_key=keys[0], **doc))
    session.add(ProjectDocumentModel(filename="trashed.pdf", storage_key=keys[1], deleted_at=now, **doc))
    session.add(
        ProjectPhotoRow(
            project_id=pid,
            uploader_user_id=owner.id,
            filename="a.jpg",
            content_type="image/jpeg",
            size_bytes=1,
            storage_key=keys[2],
            thumbnail_storage_key=keys[3],
            captured_at=now,
        )
    )
    session.add(
        ProjectAnalysisModel(project_id=pid, uploader_user_id=owner.id, title="A", storage_key=keys[4], size_bytes=1)
    )
    poste = ChiffragePosteModel(id=uuid4(), project_id=pid, name="Lot")
    session.add(poste)
    session.flush()
    session.add(ChiffrageArticleModel(poste_id=poste.id, name="With image", image_storage_key=keys[5]))
    session.add(ChiffrageArticleModel(poste_id=poste.id, name="No image"))
    invoice = InvoiceModel(
        id=uuid4(),
        project_id=pid,
        invoice_number=f"INV-{tag}",
        type="others",
        issue_date=date(2026, 9, 1),
        recipient_name="Supplier",
    )
    session.add(invoice)
    session.flush()
    attachment_key = f"invoice-attachments/{invoice.id}/x/receipt.pdf"
    session.add(
        InvoiceAttachmentModel(
            invoice_id=invoice.id,
            filename="receipt.pdf",
            storage_key=attachment_key,
            mime_type="application/pdf",
            size_bytes=1,
        )
    )
    session.commit()
    return pid, keys + [attachment_key]


@pytest.fixture
def owner(session):
    user = UserModel(id=uuid4(), email="files-owner@test.com", is_active=True)
    session.add(user)
    session.commit()
    return user


def test_reader_lists_every_key_of_the_project_and_nothing_else(session, owner):
    company_id = company_for_projects(session, owner.id)
    pid, keys = _project_with_files(session, owner, company_id, "a")
    _other, other_keys = _project_with_files(session, owner, company_id, "b")

    found = SqlAlchemyProjectStorageKeyReader(session).storage_keys_for_project(pid)

    assert sorted(found) == sorted(keys)
    assert not set(found) & set(other_keys)


def test_deleting_a_project_removes_its_files_from_storage(session, owner):
    company_id = company_for_projects(session, owner.id)
    pid, keys = _project_with_files(session, owner, company_id, "a")
    _other, other_keys = _project_with_files(session, owner, company_id, "b")
    storage = InMemoryDocumentStorage()
    for key in keys + other_keys:
        storage.put(key, BytesIO(b"x"), "application/octet-stream")

    DeleteProjectUseCase(
        SQLAlchemyProjectRepository(session),
        storage_keys=SqlAlchemyProjectStorageKeyReader(session),
        storage=storage,
    ).execute(pid)

    assert session.get(ProjectModel, pid) is None
    assert [k for k in keys if storage.head_object(k)] == []
    assert all(storage.head_object(k) for k in other_keys)
