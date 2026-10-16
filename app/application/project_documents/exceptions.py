"""Use-case-level exceptions for the project documents application layer."""


class DocumentFileTooLargeError(Exception):
    """Raised when an uploaded file exceeds the configured size limit."""

    pass


class EmptyFileError(Exception):
    """Raised when an uploaded file has zero bytes (size <= 0).

    Mapped to 400 Bad Request rather than 413 to avoid misleading the caller
    into thinking the file is too large — it is simply absent (M3).
    """

    pass


class UnsupportedDocumentTypeError(Exception):
    """Raised when an uploaded file's extension or MIME type is not in the allowlist."""

    pass


class FilenameTooLongError(ValueError):
    """Raised when a document name is longer than MAX_DISPLAY_FILENAME_LENGTH characters (422)."""

    pass


class DocumentPermissionDeniedError(Exception):
    """Raised when a user lacks permission to perform an action on a document."""

    pass


class DuplicateDocumentNameError(Exception):
    """Raised when a rename would give a document the name of another live document of the project."""
