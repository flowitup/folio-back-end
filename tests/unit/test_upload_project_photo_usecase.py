"""Unit tests for UploadProjectPhotoUseCase — orphan cleanup + validation branches."""

from __future__ import annotations

from io import BytesIO
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from PIL import Image

from app.application.project_photos.exceptions import (
    EmptyImageError,
    ImageTooLargeError,
    ThumbnailGenerationError,
    UnsupportedImageTypeError,
)
from app.application.project_photos.ports import IDocumentStorage, IProjectPhotoRepository
from app.application.project_photos.upload_project_photo import (
    MAX_SIZE_BYTES,
    MAX_VIDEO_SIZE_BYTES,
    UploadProjectPhotoUseCase,
    validate_media_type,
)
from app.domain.project_photo import ProjectPhoto
from app.infrastructure.adapters.pillow_image_thumbnailer import PillowImageThumbnailer
from app.infrastructure.adapters.werkzeug_filename_sanitizer import WerkzeugFilenameSanitizer

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_jpeg_bytes(width: int = 50, height: int = 50) -> bytes:
    """Produce minimal real JPEG bytes for tests that need valid image data."""
    img = Image.new("RGB", (width, height), color=(100, 150, 200))
    buf = BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def _make_repo() -> MagicMock:
    repo = MagicMock(spec=IProjectPhotoRepository)
    repo.save.side_effect = lambda photo: photo
    return repo


def _make_storage() -> MagicMock:
    return MagicMock(spec=IDocumentStorage)


def _make_session() -> MagicMock:
    session = MagicMock()
    session.commit = MagicMock()
    return session


def _make_use_case(repo=None, storage=None, session=None, thumbnailer=None):
    repo = repo or _make_repo()
    storage = storage or _make_storage()
    session = session or _make_session()
    thumbnailer = thumbnailer or PillowImageThumbnailer()
    uc = UploadProjectPhotoUseCase(
        repo=repo,
        storage=storage,
        thumbnailer=thumbnailer,
        db_session=session,
        filename_sanitizer=WerkzeugFilenameSanitizer(),
    )
    return uc, repo, storage, session


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestUploadHappyPath:
    def test_returns_saved_photo(self):
        uc, repo, storage, session = _make_use_case()
        data = _make_jpeg_bytes()
        result = uc.execute(
            project_id=uuid4(),
            filename="photo.jpg",
            content_type="image/jpeg",
            size_bytes=len(data),
            data=data,
            uploader_user_id=uuid4(),
            caption="Progress shot",
            captured_at=None,
        )
        assert isinstance(result, ProjectPhoto)
        assert result.filename == "photo.jpg"
        assert result.caption == "Progress shot"

    def test_storage_put_called_twice_original_and_thumb(self):
        """Both original and thumbnail must be stored."""
        uc, repo, storage, session = _make_use_case()
        data = _make_jpeg_bytes()
        uc.execute(
            project_id=uuid4(),
            filename="site.jpg",
            content_type="image/jpeg",
            size_bytes=len(data),
            data=data,
            uploader_user_id=uuid4(),
            caption=None,
            captured_at=None,
        )
        assert storage.put.call_count == 2

    def test_db_commit_called_once(self):
        uc, repo, storage, session = _make_use_case()
        data = _make_jpeg_bytes()
        uc.execute(
            project_id=uuid4(),
            filename="img.jpg",
            content_type="image/jpeg",
            size_bytes=len(data),
            data=data,
            uploader_user_id=uuid4(),
            caption=None,
            captured_at=None,
        )
        session.commit.assert_called_once()

    def test_original_key_contains_project_and_photo_ids(self):
        uc, repo, storage, session = _make_use_case()
        data = _make_jpeg_bytes()
        project_id = uuid4()
        uc.execute(
            project_id=project_id,
            filename="photo.jpg",
            content_type="image/jpeg",
            size_bytes=len(data),
            data=data,
            uploader_user_id=uuid4(),
            caption=None,
            captured_at=None,
        )
        first_put_key = storage.put.call_args_list[0][0][0]
        assert str(project_id) in first_put_key
        assert "original" in first_put_key

    def test_thumbnail_key_ends_with_thumb_jpg(self):
        uc, repo, storage, session = _make_use_case()
        data = _make_jpeg_bytes()
        uc.execute(
            project_id=uuid4(),
            filename="photo.jpg",
            content_type="image/jpeg",
            size_bytes=len(data),
            data=data,
            uploader_user_id=uuid4(),
            caption=None,
            captured_at=None,
        )
        thumb_put_key = storage.put.call_args_list[1][0][0]
        assert thumb_put_key.endswith("thumb.jpg")


# ---------------------------------------------------------------------------
# Size validation
# ---------------------------------------------------------------------------


class TestSizeValidation:
    def test_empty_raises_empty_image_error(self):
        uc, _, storage, _ = _make_use_case()
        with pytest.raises(EmptyImageError):
            uc.execute(
                project_id=uuid4(),
                filename="empty.jpg",
                content_type="image/jpeg",
                size_bytes=0,
                data=b"",
                uploader_user_id=uuid4(),
                caption=None,
                captured_at=None,
            )
        storage.put.assert_not_called()

    def test_negative_size_raises_empty_image_error(self):
        uc, _, storage, _ = _make_use_case()
        with pytest.raises(EmptyImageError):
            uc.execute(
                project_id=uuid4(),
                filename="img.jpg",
                content_type="image/jpeg",
                size_bytes=-1,
                data=b"x",
                uploader_user_id=uuid4(),
                caption=None,
                captured_at=None,
            )
        storage.put.assert_not_called()

    def test_oversize_raises_image_too_large_error(self):
        uc, _, storage, _ = _make_use_case()
        with pytest.raises(ImageTooLargeError):
            uc.execute(
                project_id=uuid4(),
                filename="huge.jpg",
                content_type="image/jpeg",
                size_bytes=MAX_SIZE_BYTES + 1,
                data=b"x",
                uploader_user_id=uuid4(),
                caption=None,
                captured_at=None,
            )
        storage.put.assert_not_called()

    def test_exactly_max_size_accepted(self):
        uc, repo, storage, session = _make_use_case()
        data = _make_jpeg_bytes()
        # size_bytes is the declared size — the use-case validates declared size,
        # not the length of actual data bytes, before calling storage.
        result = uc.execute(
            project_id=uuid4(),
            filename="max.jpg",
            content_type="image/jpeg",
            size_bytes=MAX_SIZE_BYTES,
            data=data,  # actual small JPEG; size_bytes is the declared header value
            uploader_user_id=uuid4(),
            caption=None,
            captured_at=None,
        )
        assert result is not None


# ---------------------------------------------------------------------------
# Type validation
# ---------------------------------------------------------------------------


class TestTypeValidation:
    def test_txt_extension_raises_unsupported_type(self):
        uc, _, storage, _ = _make_use_case()
        with pytest.raises(UnsupportedImageTypeError):
            uc.execute(
                project_id=uuid4(),
                filename="notes.txt",
                content_type="text/plain",
                size_bytes=10,
                data=b"x" * 10,
                uploader_user_id=uuid4(),
                caption=None,
                captured_at=None,
            )
        storage.put.assert_not_called()

    def test_disallowed_mime_raises_unsupported_type(self):
        uc, _, storage, _ = _make_use_case()
        with pytest.raises(UnsupportedImageTypeError):
            uc.execute(
                project_id=uuid4(),
                filename="image.jpg",
                content_type="application/pdf",  # valid ext but disallowed MIME
                size_bytes=10,
                data=b"x" * 10,
                uploader_user_id=uuid4(),
                caption=None,
                captured_at=None,
            )
        storage.put.assert_not_called()

    def test_octet_stream_mime_with_jpg_ext_accepted(self):
        """application/octet-stream is a MIME fallback — should still upload if ext is valid."""
        uc, repo, storage, session = _make_use_case()
        data = _make_jpeg_bytes()
        result = uc.execute(
            project_id=uuid4(),
            filename="photo.jpg",
            content_type="application/octet-stream",
            size_bytes=len(data),
            data=data,
            uploader_user_id=uuid4(),
            caption=None,
            captured_at=None,
        )
        assert result is not None


def _fake_video_thumbnailer():
    """Thumbnailer stub that returns canned JPEG bytes — no ffmpeg needed."""
    tn = MagicMock()
    tn.generate.return_value = _make_jpeg_bytes()
    return tn


class TestVideoUpload:
    def test_mp4_upload_persists_video(self):
        uc, repo, storage, session = _make_use_case(thumbnailer=_fake_video_thumbnailer())
        result = uc.execute(
            project_id=uuid4(),
            filename="walkthrough.mp4",
            content_type="video/mp4",
            size_bytes=10 * 1024 * 1024,
            data=b"fake-video-bytes",
            uploader_user_id=uuid4(),
            caption="Site walkthrough",
            captured_at=None,
        )
        assert isinstance(result, ProjectPhoto)
        assert result.content_type == "video/mp4"
        # Original + poster thumbnail both stored.
        assert storage.put.call_count == 2

    def test_video_uses_larger_cap(self):
        """A 40 MiB video is accepted (over the 25 MiB image cap, under 50 MiB)."""
        uc, _, storage, _ = _make_use_case(thumbnailer=_fake_video_thumbnailer())
        result = uc.execute(
            project_id=uuid4(),
            filename="clip.webm",
            content_type="video/webm",
            size_bytes=40 * 1024 * 1024,
            data=b"v",
            uploader_user_id=uuid4(),
            caption=None,
            captured_at=None,
        )
        assert result.content_type == "video/webm"

    def test_oversize_video_raises(self):
        uc, _, storage, _ = _make_use_case(thumbnailer=_fake_video_thumbnailer())
        with pytest.raises(ImageTooLargeError):
            uc.execute(
                project_id=uuid4(),
                filename="big.mp4",
                content_type="video/mp4",
                size_bytes=MAX_VIDEO_SIZE_BYTES + 1,
                data=b"v",
                uploader_user_id=uuid4(),
                caption=None,
                captured_at=None,
            )
        storage.put.assert_not_called()


class TestValidateMediaTypeHelper:
    def test_allowed_jpeg(self):
        assert validate_media_type("photo.jpg", "image/jpeg") == "image"

    def test_allowed_png(self):
        assert validate_media_type("img.png", "image/png") == "image"

    def test_allowed_webp(self):
        assert validate_media_type("img.webp", "image/webp") == "image"

    def test_allowed_video_mp4(self):
        assert validate_media_type("clip.mp4", "video/mp4") == "video"

    def test_allowed_video_webm(self):
        assert validate_media_type("clip.webm", "video/webm") == "video"

    def test_allowed_video_mov(self):
        assert validate_media_type("clip.mov", "video/quicktime") == "video"

    def test_unsupported_extension_raises(self):
        with pytest.raises(UnsupportedImageTypeError, match="Extension"):
            validate_media_type("doc.pdf", "application/pdf")

    def test_disallowed_mime_raises(self):
        with pytest.raises(UnsupportedImageTypeError, match="MIME"):
            validate_media_type("photo.jpg", "text/html")

    def test_disallowed_video_mime_raises(self):
        with pytest.raises(UnsupportedImageTypeError, match="MIME"):
            validate_media_type("clip.mp4", "video/x-msvideo")

    def test_octet_stream_allowed_as_fallback(self):
        assert validate_media_type("photo.png", "application/octet-stream") == "image"

    def test_octet_stream_video_fallback(self):
        assert validate_media_type("clip.mp4", "application/octet-stream") == "video"


# ---------------------------------------------------------------------------
# Orphan cleanup
# ---------------------------------------------------------------------------


class TestOrphanCleanup:
    """Both storage keys must be deleted when repo.save/commit raises."""

    def test_commit_failure_deletes_both_keys(self):
        repo = _make_repo()
        storage = _make_storage()
        session = _make_session()
        session.commit.side_effect = RuntimeError("DB is down")

        uc = UploadProjectPhotoUseCase(
            repo=repo,
            storage=storage,
            thumbnailer=PillowImageThumbnailer(),
            db_session=session,
            filename_sanitizer=WerkzeugFilenameSanitizer(),
        )

        data = _make_jpeg_bytes()
        with pytest.raises(RuntimeError, match="DB is down"):
            uc.execute(
                project_id=uuid4(),
                filename="photo.jpg",
                content_type="image/jpeg",
                size_bytes=len(data),
                data=data,
                uploader_user_id=uuid4(),
                caption=None,
                captured_at=None,
            )

        # Both original and thumb were stored
        assert storage.put.call_count == 2
        # Both must be cleaned up
        assert storage.delete.call_count == 2
        put_keys = {c[0][0] for c in storage.put.call_args_list}
        delete_keys = {c[0][0] for c in storage.delete.call_args_list}
        assert put_keys == delete_keys

    def test_repo_save_failure_deletes_both_keys(self):
        repo = _make_repo()
        repo.save.side_effect = Exception("constraint violation")
        storage = _make_storage()
        session = _make_session()

        uc = UploadProjectPhotoUseCase(
            repo=repo,
            storage=storage,
            thumbnailer=PillowImageThumbnailer(),
            db_session=session,
            filename_sanitizer=WerkzeugFilenameSanitizer(),
        )

        data = _make_jpeg_bytes()
        with pytest.raises(Exception, match="constraint violation"):
            uc.execute(
                project_id=uuid4(),
                filename="photo.jpg",
                content_type="image/jpeg",
                size_bytes=len(data),
                data=data,
                uploader_user_id=uuid4(),
                caption=None,
                captured_at=None,
            )

        assert storage.put.call_count == 2
        assert storage.delete.call_count == 2

    def test_original_error_propagates_even_if_delete_also_fails(self):
        repo = _make_repo()
        storage = _make_storage()
        storage.delete.side_effect = OSError("storage unreachable")
        session = _make_session()
        session.commit.side_effect = RuntimeError("DB is down")

        uc = UploadProjectPhotoUseCase(
            repo=repo,
            storage=storage,
            thumbnailer=PillowImageThumbnailer(),
            db_session=session,
            filename_sanitizer=WerkzeugFilenameSanitizer(),
        )

        data = _make_jpeg_bytes()
        # Original DB error must surface, not the storage.delete error
        with pytest.raises(RuntimeError, match="DB is down"):
            uc.execute(
                project_id=uuid4(),
                filename="photo.jpg",
                content_type="image/jpeg",
                size_bytes=len(data),
                data=data,
                uploader_user_id=uuid4(),
                caption=None,
                captured_at=None,
            )

    def test_corrupt_image_raises_thumbnail_generation_error_before_storage(self):
        """A corrupt image fails at thumbnail step — storage.put should not be called."""
        uc, repo, storage, session = _make_use_case()

        with pytest.raises(ThumbnailGenerationError):
            uc.execute(
                project_id=uuid4(),
                filename="corrupt.jpg",
                content_type="image/jpeg",
                size_bytes=20,
                data=b"not an image at all!!",
                uploader_user_id=uuid4(),
                caption=None,
                captured_at=None,
            )

        storage.put.assert_not_called()
        storage.delete.assert_not_called()
