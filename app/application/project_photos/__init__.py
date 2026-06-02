"""Project photos application layer — public API re-exports."""

from app.application.project_photos.ports import (
    IDocumentStorage,
    IFilenameSanitizer,
    IImageThumbnailer,
    IProjectPhotoRepository,
    ITransactionalSession,
)
from app.application.project_photos.dtos import (
    PhotoListResult,
    UploadPhotoInput,
)
from app.application.project_photos.exceptions import (
    ProjectPhotoError,
    PhotoNotFoundError,
    UnsupportedImageTypeError,
    ImageTooLargeError,
    EmptyImageError,
    PhotoPermissionDeniedError,
    ThumbnailGenerationError,
)
from app.application.project_photos.upload_project_photo import (
    UploadProjectPhotoUseCase,
    MAX_SIZE_BYTES,
    ALLOWED_EXTENSIONS,
    ALLOWED_MIME_TYPES,
    validate_image_type,
)
from app.application.project_photos.list_project_photos import ListProjectPhotosUseCase
from app.application.project_photos.get_project_photo import GetProjectPhotoUseCase
from app.application.project_photos.update_project_photo import UpdateProjectPhotoUseCase
from app.application.project_photos.delete_project_photo import DeleteProjectPhotoUseCase

__all__ = [
    # Ports
    "IDocumentStorage",
    "IFilenameSanitizer",
    "IImageThumbnailer",
    "IProjectPhotoRepository",
    "ITransactionalSession",
    # DTOs
    "PhotoListResult",
    "UploadPhotoInput",
    # Exceptions
    "ProjectPhotoError",
    "PhotoNotFoundError",
    "UnsupportedImageTypeError",
    "ImageTooLargeError",
    "EmptyImageError",
    "PhotoPermissionDeniedError",
    "ThumbnailGenerationError",
    # Use cases
    "UploadProjectPhotoUseCase",
    "ListProjectPhotosUseCase",
    "GetProjectPhotoUseCase",
    "UpdateProjectPhotoUseCase",
    "DeleteProjectPhotoUseCase",
    # Upload constants / helpers
    "MAX_SIZE_BYTES",
    "ALLOWED_EXTENSIONS",
    "ALLOWED_MIME_TYPES",
    "validate_image_type",
]
