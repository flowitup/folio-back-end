"""Use-case-level exceptions for the project photos application layer."""


class ProjectPhotoError(Exception):
    """Base exception for all project photo application errors."""

    pass


class PhotoNotFoundError(ProjectPhotoError):
    """Raised when a photo does not exist, has been soft-deleted, or belongs to a different project."""

    pass


class UnsupportedImageTypeError(ProjectPhotoError):
    """Raised when an uploaded file's extension or MIME type is not in the allowed image allowlist."""

    pass


class ImageTooLargeError(ProjectPhotoError):
    """Raised when an uploaded image exceeds the configured size limit."""

    pass


class EmptyImageError(ProjectPhotoError):
    """Raised when an uploaded image has zero bytes (size <= 0).

    Mapped to 400 Bad Request rather than 413 to avoid misleading the caller
    into thinking the file is too large — it is simply absent.
    """

    pass


class PhotoPermissionDeniedError(ProjectPhotoError):
    """Raised when a user lacks permission to modify or delete a photo."""

    pass


class ThumbnailGenerationError(ProjectPhotoError):
    """Raised when the thumbnailer fails to process the image bytes."""

    pass
