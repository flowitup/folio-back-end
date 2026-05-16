"""Use-case-level exceptions for the project documents application layer."""


class DocumentFileTooLargeError(Exception):
    """Raised when an uploaded file exceeds the configured size limit or is empty."""

    pass


class UnsupportedDocumentTypeError(Exception):
    """Raised when an uploaded file's extension or MIME type is not in the allowlist."""

    pass


class DocumentPermissionDeniedError(Exception):
    """Raised when a user lacks permission to perform an action on a document."""

    pass
