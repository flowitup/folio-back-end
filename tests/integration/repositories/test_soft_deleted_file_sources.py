"""Soft-deleted file sources used by the storage janitor (QA3 documents-photos-analyses-04)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from app.domain.project_photo import ProjectPhoto
from app.infrastructure.database.repositories.soft_deleted_file_sources import SoftDeletedPhotoSource
from app.infrastructure.database.repositories.sqlalchemy_project_photo_repository import (
    SqlAlchemyProjectPhotoRepository,
)


def _photo(project_id, user_id) -> ProjectPhoto:
    photo_id = uuid4()
    now = datetime.now(timezone.utc)
    return ProjectPhoto(
        id=photo_id,
        project_id=project_id,
        uploader_user_id=user_id,
        filename="site.jpg",
        content_type="image/jpeg",
        size_bytes=10,
        storage_key=f"project-photos/{project_id}/{photo_id}/original/site.jpg",
        thumbnail_storage_key=f"project-photos/{project_id}/{photo_id}/thumb.jpg",
        caption=None,
        captured_at=now,
        created_at=now,
        deleted_at=None,
    )


def test_photo_source_lists_old_soft_deleted_photos_with_both_keys(session):
    repo = SqlAlchemyProjectPhotoRepository(session)
    project_id, user_id = uuid4(), uuid4()
    old, live = _photo(project_id, user_id), _photo(project_id, user_id)
    repo.save(old)
    repo.save(live)
    repo.soft_delete(old.id, datetime.now(timezone.utc) - timedelta(days=40))
    session.commit()

    source = SoftDeletedPhotoSource(session)
    found = source.find_soft_deleted_before(datetime.now(timezone.utc) - timedelta(days=30), 100)

    assert [c.id for c in found] == [old.id]
    assert found[0].storage_keys == (old.storage_key, old.thumbnail_storage_key)

    source.hard_delete(old.id)
    session.commit()
    assert repo.find_by_id(old.id) is None
    assert repo.find_by_id(live.id) is not None
